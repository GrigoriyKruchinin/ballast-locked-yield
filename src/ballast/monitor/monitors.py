"""Risk monitors over collected data. Research mode: events are stored and notified; no trading actions.

Monitors: peg deviation, protocol TVL drop, implied-rate spikes on whitelisted markets,
parking rate moves, and source failures (data lag). Repeated events are throttled.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from ballast.config.settings import RiskSettings
from ballast.config.whitelist import Whitelist
from ballast.domain.models import MonitorEvent, Opportunity, Severity
from ballast.domain.money import HUNDRED, pct
from ballast.sources.defillama import ProtocolTvl

THROTTLE = timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class MonitorInputs:
    observed_at: datetime
    pegs: Mapping[str, Decimal]
    tvl: Mapping[str, ProtocolTvl]
    opportunities: Sequence[Opportunity]
    previous_apy: Mapping[str, Decimal]  # opportunity key -> implied APY at the previous scan
    parking: Sequence[Opportunity]
    previous_parking_apy: Mapping[str, Decimal]
    errors: Sequence[str]


@dataclass(frozen=True, slots=True)
class RiskMonitor:
    whitelist: Whitelist
    settings: RiskSettings

    def evaluate(self, inputs: MonitorInputs) -> list[MonitorEvent]:
        at = inputs.observed_at
        events: list[MonitorEvent] = []
        events += self._pegs(inputs.pegs, at)
        events += self._tvl(inputs.tvl, at)
        events += self._rates(inputs.opportunities, inputs.previous_apy, at, parking=False)
        events += self._rates(inputs.parking, inputs.previous_parking_apy, at, parking=True)
        events += [
            MonitorEvent("source_error", Severity.WARNING, err.split(":")[0], err, None, at)
            for err in inputs.errors
        ]
        return events

    def _pegs(self, pegs: Mapping[str, Decimal], at: datetime) -> Iterable[MonitorEvent]:
        expected = {a.peg_symbol.lower() for a in self.whitelist.assets}
        for symbol in sorted(expected):
            price = pegs.get(symbol)
            if price is None:
                yield MonitorEvent("peg_missing", Severity.WARNING, symbol.upper(), "no peg price", None, at)
                continue
            deviation = abs(price - 1)
            if deviation >= pct(self.settings.depeg_critical_pct):
                yield MonitorEvent(
                    "depeg", Severity.CRITICAL, symbol.upper(), f"price {price:.4f}", price, at
                )
            elif deviation >= pct(self.settings.depeg_warn_pct):
                yield MonitorEvent("depeg", Severity.WARNING, symbol.upper(), f"price {price:.4f}", price, at)

    def _tvl(self, tvl: Mapping[str, ProtocolTvl], at: datetime) -> Iterable[MonitorEvent]:
        for protocol, row in sorted(tvl.items()):
            change = row.change_7d
            if change is None:
                continue
            if change <= -pct(self.settings.tvl_drop_critical_pct):
                yield MonitorEvent(
                    "tvl_drop", Severity.CRITICAL, protocol, f"TVL {change:+.1%} in 7d", change, at
                )
            elif change <= -pct(self.settings.tvl_drop_warn_pct):
                yield MonitorEvent(
                    "tvl_drop", Severity.WARNING, protocol, f"TVL {change:+.1%} in 7d", change, at
                )

    def _rates(
        self, opps: Sequence[Opportunity], previous: Mapping[str, Decimal], at: datetime, *, parking: bool
    ) -> Iterable[MonitorEvent]:
        limit_pp = self.settings.rate_spike_pp / (2 if parking else 1)
        for opp in opps:
            if opp.quoted_apy is None or opp.key not in previous:
                continue
            if (
                not parking
                and self.whitelist.find_asset(opp.asset_symbol, opp.chain, opp.asset_address) is None
            ):
                continue
            jump_pp = (opp.quoted_apy - previous[opp.key]) * HUNDRED
            if abs(jump_pp) >= limit_pp:
                kind = "parking_rate_move" if parking else "rate_spike"
                msg = f"{opp.asset_symbol} {previous[opp.key]:.2%} -> {opp.quoted_apy:.2%}"
                yield MonitorEvent(kind, Severity.WARNING, opp.key, msg, jump_pp, at)


def throttle(
    events: Sequence[MonitorEvent], recent: Iterable[tuple[str, str, str, datetime]]
) -> list[MonitorEvent]:
    """Drop events whose (kind, subject, severity) fired within ``THROTTLE``; escalations always pass."""
    last: dict[tuple[str, str, str], datetime] = {}
    for kind, subject, severity, at in recent:
        key = (kind, subject, severity)
        if key not in last or at > last[key]:
            last[key] = at
    out: list[MonitorEvent] = []
    for event in events:
        key = (event.kind, event.subject, event.severity.value)
        fired = last.get(key)
        if fired is not None and event.observed_at - fired < THROTTLE:
            continue
        out.append(event)
    return out
