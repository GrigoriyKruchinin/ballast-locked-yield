"""Risk rules. Each rule implements :class:`ballast.domain.interfaces.IRiskRule`.

Rules read live market state from a :class:`MarketState` snapshot injected at construction,
so ``evaluate`` stays pure. A missing data point is treated as risk, not as safety.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ballast.config.whitelist import AssetInfo, OracleDependency, Whitelist
from ballast.domain.models import Opportunity, RiskFinding, SourceKind
from ballast.domain.money import HUNDRED, ZERO, pct
from ballast.risk.incidents import Incident, IncidentCatalog


@dataclass(frozen=True, slots=True)
class MarketState:
    """Live inputs for risk rules, captured once per scan."""

    as_of: date
    protocol_tvl_usd: Mapping[str, Decimal] = field(default_factory=dict)
    protocol_tvl_change_7d: Mapping[str, Decimal] = field(default_factory=dict)  # fraction, -0.2 = -20%
    asset_usd_price: Mapping[str, Decimal] = field(default_factory=dict)  # by lowercase symbol
    parking_apy: Decimal = ZERO


def _lookup(whitelist: Whitelist, opportunity: Opportunity) -> AssetInfo | None:
    return whitelist.find_asset(opportunity.asset_symbol, opportunity.chain, opportunity.asset_address)


def _ok(rule: str, reason: str = "ok") -> RiskFinding:
    return RiskFinding(rule=rule, score=ZERO, reason=reason)


def _block(rule: str, reason: str) -> RiskFinding:
    return RiskFinding(rule=rule, score=HUNDRED, reason=reason, blocking=True)


@dataclass(frozen=True, slots=True)
class WhitelistRule:
    whitelist: Whitelist
    name: str = "whitelist"

    def evaluate(self, opportunity: Opportunity) -> RiskFinding:
        asset = _lookup(self.whitelist, opportunity)
        if asset is None:
            return _block(
                self.name, f"asset {opportunity.asset_symbol} not whitelisted (or address mismatch)"
            )
        for slug in (opportunity.venue, opportunity.protocol, *asset.protocols):
            if slug not in self.whitelist.protocols:
                return _block(self.name, f"protocol {slug} not whitelisted")
        return _ok(self.name, f"{asset.symbol} tier {asset.tier}")


@dataclass(frozen=True, slots=True)
class AddressVerifiedRule:
    """Unpinned addresses are scored in research mode and blocked in execution mode."""

    whitelist: Whitelist
    execution_mode: bool = False
    name: str = "address_verified"

    def evaluate(self, opportunity: Opportunity) -> RiskFinding:
        asset = _lookup(self.whitelist, opportunity)
        if asset is None:
            return _ok(self.name, "handled by whitelist rule")
        if self.whitelist.is_address_verified(asset, opportunity.chain, opportunity.asset_address):
            return _ok(self.name)
        reason = f"{asset.symbol} address on {opportunity.chain.slug} not pinned in whitelist"
        if self.execution_mode:
            return _block(self.name, reason)
        return RiskFinding(self.name, Decimal(5), reason)


@dataclass(frozen=True, slots=True)
class MaturityRule:
    min_days: int
    max_days: int
    name: str = "maturity"

    def evaluate(self, opportunity: Opportunity) -> RiskFinding:
        if opportunity.kind is SourceKind.PARKING:
            return _ok(self.name, "no maturity")
        days = opportunity.days_to_maturity()
        if days is None:
            return _block(self.name, "dated opportunity without maturity")
        if days < self.min_days:
            return _block(self.name, f"{days:.1f} days to maturity < {self.min_days}")
        if days > self.max_days:
            return _block(self.name, f"{days:.0f} days to maturity > {self.max_days}")
        return _ok(self.name)


@dataclass(frozen=True, slots=True)
class ProtocolMaturityRule:
    """Age and audits of every protocol the position depends on."""

    whitelist: Whitelist
    as_of: date
    name: str = "protocol_age_audits"

    def evaluate(self, opportunity: Opportunity) -> RiskFinding:
        asset = _lookup(self.whitelist, opportunity)
        slugs = {opportunity.venue, opportunity.protocol, *(asset.protocols if asset else ())}
        score = ZERO
        notes: list[str] = []
        for slug in sorted(slugs):
            info = self.whitelist.protocols.get(slug)
            if info is None:
                continue
            year, month = (int(x) for x in info.launched.split("-"))
            age_years = Decimal((self.as_of.year - year) * 12 + self.as_of.month - month) / Decimal(12)
            if age_years < 1:
                score += Decimal(15)
                notes.append(f"{slug} < 1y old")
            elif age_years < 2:
                score += Decimal(8)
                notes.append(f"{slug} < 2y old")
            if info.kind != "issuer" and info.audits < 2:
                score += Decimal(10)
                notes.append(f"{slug} has {info.audits} audits")
        return RiskFinding(self.name, score, "; ".join(notes) or "mature, audited")


_ORACLE_SCORE: Mapping[OracleDependency, Decimal] = {
    OracleDependency.NONE: ZERO,
    OracleDependency.LOW: Decimal(1),
    OracleDependency.MEDIUM: Decimal(3),
    OracleDependency.HIGH: Decimal(5),
}


@dataclass(frozen=True, slots=True)
class CounterpartyRule:
    """Centralized counterparties, admin pause/upgrade keys and oracle dependency."""

    whitelist: Whitelist
    name: str = "counterparty_admin_oracle"

    def evaluate(self, opportunity: Opportunity) -> RiskFinding:
        asset = _lookup(self.whitelist, opportunity)
        slugs = {opportunity.venue, opportunity.protocol, *(asset.protocols if asset else ())}
        score = ZERO
        notes: list[str] = []
        for slug in sorted(slugs):
            info = self.whitelist.protocols.get(slug)
            if info is None:
                continue
            if info.centralized_counterparty:
                score += Decimal(5)
                notes.append(f"{slug}: centralized counterparty")
            if info.admin_pause or info.upgradeable:
                score += Decimal(2)
            score += _ORACLE_SCORE[info.oracle_dependency]
        if asset is not None and not asset.par_redemption:
            score += Decimal(10)
            notes.append(f"{asset.symbol}: no permissionless par redemption")
        return RiskFinding(self.name, score, "; ".join(notes) or "low counterparty risk")


@dataclass(frozen=True, slots=True)
class TvlRule:
    state: MarketState
    min_tvl_usd: Decimal
    drop_warn_pct: Decimal
    drop_critical_pct: Decimal
    name: str = "tvl"

    def evaluate(self, opportunity: Opportunity) -> RiskFinding:
        slug = opportunity.protocol
        tvl = self.state.protocol_tvl_usd.get(slug)
        if tvl is None:
            return RiskFinding(self.name, Decimal(10), f"{slug}: TVL unknown")
        if tvl < self.min_tvl_usd:
            return _block(self.name, f"{slug}: TVL {tvl:,.0f} < {self.min_tvl_usd:,.0f}")
        change = self.state.protocol_tvl_change_7d.get(slug, ZERO)
        if change <= -pct(self.drop_critical_pct):
            return _block(self.name, f"{slug}: TVL down {-change:.0%} in 7d")
        if change <= -pct(self.drop_warn_pct):
            return RiskFinding(self.name, Decimal(15), f"{slug}: TVL down {-change:.0%} in 7d")
        return _ok(self.name)


@dataclass(frozen=True, slots=True)
class LiquidityRule:
    min_liquidity_usd: Decimal
    name: str = "liquidity"

    def evaluate(self, opportunity: Opportunity) -> RiskFinding:
        if opportunity.kind is SourceKind.PARKING:
            return _ok(self.name)
        if opportunity.liquidity_usd < self.min_liquidity_usd:
            return _block(
                self.name,
                f"liquidity {opportunity.liquidity_usd:,.0f} < {self.min_liquidity_usd:,.0f}",
            )
        return _ok(self.name)


@dataclass(frozen=True, slots=True)
class PegRule:
    """Checks the price of the peg asset (e.g. USDe for sUSDe, USDC for aUSDC) against 1 USD."""

    state: MarketState
    whitelist: Whitelist
    warn_pct: Decimal
    critical_pct: Decimal
    name: str = "peg"

    def evaluate(self, opportunity: Opportunity) -> RiskFinding:
        asset = _lookup(self.whitelist, opportunity)
        peg_symbol = asset.peg_symbol if asset else opportunity.asset_symbol
        price = self.state.asset_usd_price.get(peg_symbol.lower())
        if price is None:
            return RiskFinding(self.name, Decimal(10), f"{peg_symbol}: no peg price")
        deviation = abs(price - Decimal(1))
        if deviation >= pct(self.critical_pct):
            return _block(self.name, f"{peg_symbol} at {price} (off peg {deviation:.2%})")
        if deviation >= pct(self.warn_pct):
            return RiskFinding(self.name, Decimal(20), f"{peg_symbol} off peg {deviation:.2%}")
        return _ok(self.name)


@dataclass(frozen=True, slots=True)
class DepegHistoryRule:
    """Scores recorded incidents of the asset, its peg asset (USDe for sUSDe) and its protocols."""

    catalog: IncidentCatalog
    whitelist: Whitelist
    points_per_incident: Decimal = Decimal(4)
    points_per_loss_pct: Decimal = Decimal(2)
    name: str = "depeg_history"

    def evaluate(self, opportunity: Opportunity) -> RiskFinding:
        asset = _lookup(self.whitelist, opportunity)
        symbols = {opportunity.asset_symbol, *((asset.symbol, asset.peg_symbol) if asset else ())}
        protocols = {opportunity.protocol, *(asset.protocols if asset else ())}
        seen: dict[str, Incident] = {}
        for symbol in sorted(symbols):
            seen.update({i.id: i for i in self.catalog.for_asset(symbol)})
        for slug in sorted(protocols):
            seen.update({i.id: i for i in self.catalog.for_protocol(slug)})
        if not seen:
            return _ok(self.name, "no recorded incidents")
        incidents = sorted(seen.values(), key=lambda i: i.date, reverse=True)
        score = sum(
            (self.points_per_incident + self.points_per_loss_pct * i.holder_loss_pct for i in incidents),
            ZERO,
        )
        ids = ", ".join(i.id for i in incidents[:5])
        return RiskFinding(self.name, min(score, Decimal(40)), f"{len(incidents)} incidents: {ids}")


@dataclass(frozen=True, slots=True)
class YieldAnomalyRule:
    """A fixed rate far above parking is the market pricing in risk."""

    state: MarketState
    soft_excess_pp: Decimal = Decimal(5)
    max_excess_pp: Decimal = Decimal(15)
    name: str = "yield_anomaly"

    def evaluate(self, opportunity: Opportunity) -> RiskFinding:
        if opportunity.quoted_apy is None or opportunity.kind is SourceKind.PARKING:
            return _ok(self.name)
        excess_pp = (opportunity.quoted_apy - self.state.parking_apy) * HUNDRED
        if excess_pp > self.max_excess_pp:
            return _block(self.name, f"implied APY {excess_pp:.1f} pp above parking: too good to be true")
        if excess_pp > self.soft_excess_pp:
            span = self.max_excess_pp - self.soft_excess_pp
            score = Decimal(20) * (excess_pp - self.soft_excess_pp) / span
            return RiskFinding(self.name, score, f"implied APY {excess_pp:.1f} pp above parking")
        return _ok(self.name)
