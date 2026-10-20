"""Backtest: replay the live decision logic (Analyzer + PaperEngine) over historical data.

Out-of-sample protocol: tier premiums are chosen on the *tuning* window only; every reported
metric comes from the *validation* window, which tuning never sees.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal

from ballast.backtest.data import HistoricalData
from ballast.config.settings import AllocationSettings, CostSettings, RiskSettings
from ballast.config.whitelist import Whitelist
from ballast.costs.model import GasSnapshot, RoundTripCostModel
from ballast.domain.models import Chain, NlyResult, Opportunity, Tier
from ballast.domain.money import DAYS_PER_MONTH, DAYS_PER_YEAR, ONE, ZERO
from ballast.nly.calculator import NlyCalculator
from ballast.pipeline.analyze import AnalysisInputs, Analyzer
from ballast.portfolio.paper import PaperEngine, PaperState
from ballast.risk.incidents import IncidentCatalog
from ballast.risk.rules import MarketState

BIG_TVL = Decimal(10**12)


@dataclass(frozen=True, slots=True)
class BacktestAssumptions:
    """What history cannot tell us. Reported verbatim in the backtest report."""

    gas_gwei: Mapping[Chain, Decimal] = field(
        default_factory=lambda: {
            Chain.ETHEREUM: Decimal(3),
            Chain.ARBITRUM: Decimal("0.02"),
            Chain.BASE: Decimal("0.01"),
        }
    )
    l1_fee_usd: Mapping[Chain, Decimal] = field(
        default_factory=lambda: {
            Chain.ETHEREUM: ZERO,
            Chain.ARBITRUM: Decimal("0.02"),
            Chain.BASE: Decimal("0.02"),
        }
    )
    eth_usd_fallback: Decimal = Decimal(3000)
    peg_usd: Decimal = ONE  # depeg losses are applied separately from the incident catalog
    protocol_tvl_ok: bool = True  # historical TVL screens assumed passed (survivorship caveat)
    step: timedelta = timedelta(days=1)
    universe_a: int = 8
    universe_b: int = 30

    def describe(self) -> list[str]:
        gas = ", ".join(
            f"{c.slug} {g} gwei + L1 {self.l1_fee_usd.get(c, ZERO)} USD" for c, g in self.gas_gwei.items()
        )
        return [
            f"Gas: {gas} (no free hosted L2/L1 gas history; Ethereum set conservatively high).",
            f"ETH price from DefiLlama daily chart (fallback {self.eth_usd_fallback} USD).",
            "Stablecoins assumed at peg while held; incident-catalog expected losses subtracted afterwards.",
            "Protocol TVL / peg screens assumed passed historically (data not available point-in-time).",
            "PT price derived from hourly implied APY: (1 + APY) ** (-days / 365).",
            "Slippage from the pool-TVL heuristic (no historical quotes exist).",
            f"Decision step: {self.step}.",
        ]


@dataclass(frozen=True, slots=True)
class DayRecord:
    day: date
    total_usd: Decimal
    locked_usd: Decimal
    parked_usd: Decimal
    idle_usd: Decimal
    parking_ref_apy: Decimal
    exposure: Mapping[str, Decimal]


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    days: int
    start_value: Decimal
    end_value: Decimal
    net_return: Decimal
    net_apy: Decimal
    net_monthly: Decimal
    parking_apy_avg: Decimal
    excess_pp: Decimal
    weeks_total: int
    weeks_deployed_70: int
    avg_locked_share: Decimal
    costs_usd: Decimal
    gross_yield_usd: Decimal
    cost_share_of_gross: Decimal | None
    max_protocol_share: Decimal
    max_tier_b_share: Decimal
    avg_tier_a_locked_share: Decimal
    avg_tier_b_locked_share: Decimal
    incident_loss_apy: Decimal
    net_apy_after_incidents: Decimal
    positions_opened: int
    days_with_allowed_candidate: int = 0
    median_best_excess_pp: Decimal | None = None
    max_best_excess_pp: Decimal | None = None

    @property
    def weeks_deployed_share(self) -> Decimal:
        return Decimal(self.weeks_deployed_70) / Decimal(self.weeks_total) if self.weeks_total else ZERO


@dataclass(frozen=True, slots=True)
class BacktestResult:
    label: str
    start: datetime
    end: datetime
    premiums: tuple[Decimal, Decimal]
    chains: tuple[Chain, ...]
    metrics: BacktestMetrics
    records: tuple[DayRecord, ...]
    opened: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Backtester:
    data: HistoricalData
    whitelist: Whitelist
    catalog: IncidentCatalog
    allocation: AllocationSettings
    costs: CostSettings
    assumptions: BacktestAssumptions = field(default_factory=BacktestAssumptions)

    def run(
        self,
        label: str,
        start: datetime,
        end: datetime,
        chains: Sequence[Chain],
        premiums: tuple[Decimal, Decimal],
    ) -> BacktestResult:
        risk = RiskSettings(tier_a_premium_pp=premiums[0], tier_b_premium_pp=premiums[1])
        analyzer = Analyzer(self.whitelist, self.catalog, risk, self.allocation, self.costs)
        state = PaperState.new(label, self.allocation.paper_capital_usd, start)
        records: list[DayRecord] = []
        opened: list[str] = []
        best_excess: list[Decimal] = []
        at = start
        while at <= end:
            gas = self._gas(at, chains)
            inputs = AnalysisInputs(
                opportunities=self.data.opportunities_at(at, self.whitelist, chains),
                parking=self.data.parking_at(at, chains),
                gas=gas,
                state=self._state(at),
            )
            engine = _engine(NlyCalculator(analyzer.cost_model(inputs)), at, gas)
            rates = {p.key: p.quoted_apy for p in inputs.parking if p.quoted_apy is not None}
            engine.accrue_and_mature(state, at, rates, {}, lambda s: s)
            analysis = analyzer.run(
                inputs,
                capital_usd=state.total_value(at),
                now=at,
                chains=chains,
                existing=state.existing_allocations(),
            )
            allowed = [c.excess_apy for c in analysis.candidates if c.risk.allowed]
            if allowed:
                best_excess.append(max(allowed))
            by_key = {c.opportunity.key: c.opportunity for c in analysis.candidates}
            new, _, _ = engine.apply_plan(state, analysis.plan, by_key, at)
            opened += [p.key for p in new]
            records.append(self._record(state, at, analysis.parking_apy))
            at += self.assumptions.step
        metrics = self._metrics(state, records, len(opened))
        if best_excess:
            ordered = sorted(best_excess)
            metrics = replace(
                metrics,
                days_with_allowed_candidate=len(best_excess),
                median_best_excess_pp=ordered[len(ordered) // 2] * 100,
                max_best_excess_pp=ordered[-1] * 100,
            )
        return BacktestResult(
            label, start, end, premiums, tuple(chains), metrics, tuple(records), tuple(opened)
        )

    def _gas(self, at: datetime, chains: Sequence[Chain]) -> dict[Chain, GasSnapshot]:
        eth = self.data.eth_usd.at(at) or self.assumptions.eth_usd_fallback
        out: dict[Chain, GasSnapshot] = {}
        for chain in chains:
            gwei = self.assumptions.gas_gwei.get(chain)
            if gwei is None:
                continue
            wei = int(gwei * Decimal(10**9))
            out[chain] = GasSnapshot(chain, wei, eth, self.assumptions.l1_fee_usd.get(chain, ZERO))
        return out

    def _state(self, at: datetime) -> MarketState:
        pegs = {a.peg_symbol.lower(): self.assumptions.peg_usd for a in self.whitelist.assets}
        tvl = dict.fromkeys(self.whitelist.protocols, BIG_TVL) if self.assumptions.protocol_tvl_ok else {}
        return MarketState(as_of=at.date(), protocol_tvl_usd=tvl, asset_usd_price=pegs)

    @staticmethod
    def _record(state: PaperState, at: datetime, parking_apy: Decimal) -> DayRecord:
        exposure: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
        for pos in state.locked:
            value = pos.book_value(at)
            for key in pos.exposures:
                exposure[key] += value
        for bal in state.parking.values():
            for key in bal.exposures:
                exposure[key] += bal.balance_usd
        return DayRecord(
            day=at.date(),
            total_usd=state.total_value(at),
            locked_usd=state.locked_value(at),
            parked_usd=state.parked_value(),
            idle_usd=state.idle_usd,
            parking_ref_apy=parking_apy,
            exposure=dict(exposure),
        )

    def _metrics(self, state: PaperState, records: Sequence[DayRecord], n_opened: int) -> BacktestMetrics:
        if not records:
            msg = "empty backtest window"
            raise ValueError(msg)
        start_value = state.initial_capital_usd
        end_value = records[-1].total_usd
        days = Decimal(max((records[-1].day - records[0].day).days, 1))
        net_return = end_value / start_value - 1
        parking_avg = sum((r.parking_ref_apy for r in records), ZERO) / len(records)
        net_apy = net_return * DAYS_PER_YEAR / days

        weeks: defaultdict[tuple[int, int], list[Decimal]] = defaultdict(list)
        for r in records:
            iso = r.day.isocalendar()
            weeks[(iso.year, iso.week)].append(r.locked_usd / r.total_usd if r.total_usd else ZERO)
        weekly = [sum(v, ZERO) / len(v) for v in weeks.values()]
        avg_locked = sum((r.locked_usd / r.total_usd for r in records if r.total_usd), ZERO) / len(records)

        def share(r: DayRecord, prefix: str) -> Decimal:
            vals = [v for k, v in r.exposure.items() if k.startswith(prefix)]
            return max(vals, default=ZERO) / r.total_usd if r.total_usd else ZERO

        def locked_tier(r: DayRecord, tier: str) -> Decimal:
            return r.exposure.get(f"tier:{tier}", ZERO) / r.total_usd if r.total_usd else ZERO

        max_protocol = max(share(r, "protocol:") for r in records)
        max_tier_b = max(locked_tier(r, "B") for r in records)
        # Parking contributes tier:A exposure too; incident adjustment applies to all tier exposure.
        avg_a = sum((locked_tier(r, "A") for r in records), ZERO) / len(records)
        avg_b = sum((locked_tier(r, "B") for r in records), ZERO) / len(records)
        first = date(2022, 1, 1)
        last = records[-1].day
        loss_a = self.catalog.annual_expected_loss(Tier.A, first, last, self.assumptions.universe_a)
        loss_b = self.catalog.annual_expected_loss(Tier.B, first, last, self.assumptions.universe_b)
        incident_apy = avg_a * loss_a + avg_b * loss_b
        gross = end_value - start_value + state.costs_paid_usd
        return BacktestMetrics(
            days=int(days),
            start_value=start_value,
            end_value=end_value,
            net_return=net_return,
            net_apy=net_apy,
            net_monthly=net_return * DAYS_PER_MONTH / days,
            parking_apy_avg=parking_avg,
            excess_pp=(net_apy - parking_avg) * 100,
            weeks_total=len(weekly),
            weeks_deployed_70=sum(1 for w in weekly if w >= Decimal("0.7")),
            avg_locked_share=avg_locked,
            costs_usd=state.costs_paid_usd,
            gross_yield_usd=gross,
            cost_share_of_gross=state.costs_paid_usd / gross if gross > 0 else None,
            max_protocol_share=max_protocol,
            max_tier_b_share=max_tier_b,
            avg_tier_a_locked_share=avg_a,
            avg_tier_b_locked_share=avg_b,
            incident_loss_apy=incident_apy,
            net_apy_after_incidents=net_apy - incident_apy,
            positions_opened=n_opened,
        )


def _engine(calc: NlyCalculator, at: datetime, gas: Mapping[Chain, GasSnapshot]) -> PaperEngine:
    units = calc.cost_model.parking_gas_units if isinstance(calc.cost_model, RoundTripCostModel) else 250_000

    def evaluate(opp: Opportunity, size: Decimal) -> NlyResult:
        return calc.evaluate(opp, size, at)

    def parking_cost(chain: Chain) -> Decimal:
        snap = gas.get(chain)
        return snap.tx_cost_usd(units) if snap else Decimal("0.5")

    return PaperEngine(evaluate=evaluate, parking_exit_cost=parking_cost)


PREMIUM_GRID: tuple[tuple[Decimal, Decimal], ...] = tuple(
    (Decimal(a), Decimal(b)) for a in (1, 2, 3) for b in (4, 6, 8)
)


@dataclass(frozen=True, slots=True)
class OutOfSampleReport:
    tuning: tuple[BacktestResult, ...]
    chosen: tuple[Decimal, Decimal]
    validation_chosen: BacktestResult
    validation_default: BacktestResult


def out_of_sample(
    bt: Backtester,
    start: datetime,
    end: datetime,
    chains: Sequence[Chain],
    *,
    tuning_share: Decimal = Decimal("0.6"),
    default: tuple[Decimal, Decimal] = (Decimal(2), Decimal(6)),
    grid: Sequence[tuple[Decimal, Decimal]] = PREMIUM_GRID,
) -> OutOfSampleReport:
    split = start + timedelta(seconds=float(Decimal((end - start).total_seconds()) * tuning_share))
    tuning = tuple(bt.run(f"tune-{a}-{b}", start, split, chains, (a, b)) for a, b in grid)
    best = max(tuning, key=lambda r: r.metrics.net_apy_after_incidents)
    validation_chosen = bt.run("validation-chosen", split, end, chains, best.premiums)
    validation_default = (
        validation_chosen
        if best.premiums == default
        else bt.run("validation-default", split, end, chains, default)
    )
    return OutOfSampleReport(tuning, best.premiums, validation_chosen, validation_default)
