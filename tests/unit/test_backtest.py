from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ballast.backtest.data import DailySeries, HistoricalData, MarketSeries, ParkingSeries
from ballast.backtest.engine import Backtester, out_of_sample
from ballast.config.settings import AllocationSettings, CostSettings
from ballast.config.whitelist import load_whitelist
from ballast.domain.models import Chain
from ballast.risk.incidents import load_incidents

ROOT = Path(__file__).resolve().parents[2]
WL = load_whitelist(ROOT / "config" / "whitelist.yaml")
CAT = load_incidents(ROOT / "config" / "incidents.yaml")
START = datetime(2025, 1, 1, tzinfo=UTC)
SUSDS = "0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD"


def hourly(days: int) -> tuple[datetime, ...]:
    return tuple(START + timedelta(hours=h) for h in range(days * 24))


def market(apy: str, expiry_days: int, name: str = "sUSDS", underlying: str = SUSDS) -> MarketSeries:
    times = hourly(expiry_days)
    return MarketSeries(
        chain=Chain.ETHEREUM,
        market=f"0x{name.lower()}{expiry_days}",
        name=name,
        protocol_name="Sky",
        underlying=underlying,
        expiry=START + timedelta(days=expiry_days),
        fee_rate=Decimal("0.001"),
        times=times,
        implied_apy=tuple(Decimal(apy) for _ in times),
        tvl_usd=tuple(Decimal(20_000_000) for _ in times),
    )


def data(*markets: MarketSeries, parking_apy: str = "0.04") -> HistoricalData:
    days = tuple(START + timedelta(days=d) for d in range(400))
    park = ParkingSeries(
        key="parking:aave-v3:ethereum:USDC:aa70268e",
        protocol="aave-v3",
        chain=Chain.ETHEREUM,
        asset_symbol="USDC",
        asset_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        tvl_usd=Decimal(10**8),
        apy=DailySeries(days, tuple(Decimal(parking_apy) for _ in days)),
    )
    eth = DailySeries(days, tuple(Decimal(3000) for _ in days))
    return HistoricalData(list(markets), [park], eth)


def bt(d: HistoricalData) -> Backtester:
    return Backtester(d, WL, CAT, AllocationSettings(), CostSettings())


def test_attractive_market_entered_and_redeemed() -> None:
    result = bt(data(market("0.12", 120))).run(
        "t", START, START + timedelta(days=150), [Chain.ETHEREUM], (Decimal(2), Decimal(6))
    )
    m = result.metrics
    assert result.opened  # entered the 12% market (threshold ~4% + 2% + score premium)
    assert m.end_value > m.start_value
    assert m.positions_opened == 1  # duplicate entries into the same market are ignored
    # parking capped at 35% per protocol: aave only -> part idle; return between parking-only and full PT
    assert Decimal(0) < m.net_apy < Decimal("0.12")
    assert m.max_protocol_share <= Decimal("0.36")
    assert m.cost_share_of_gross is not None


def test_unattractive_market_skipped() -> None:
    result = bt(data(market("0.045", 120))).run(
        "t", START, START + timedelta(days=60), [Chain.ETHEREUM], (Decimal(2), Decimal(6))
    )
    assert result.opened == ()
    assert result.metrics.avg_locked_share == 0
    assert result.metrics.weeks_deployed_70 == 0


def test_unknown_asset_never_traded() -> None:
    exotic = market("0.30", 120, name="USD3", underlying="0x0000000000000000000000000000000000000003")
    result = bt(data(exotic)).run(
        "t", START, START + timedelta(days=30), [Chain.ETHEREUM], (Decimal(2), Decimal(6))
    )
    assert result.opened == ()


def test_out_of_sample_uses_disjoint_windows() -> None:
    d = data(market("0.12", 300))
    end = START + timedelta(days=100)
    grid = ((Decimal(1), Decimal(4)), (Decimal(3), Decimal(8)))
    report = out_of_sample(bt(d), START, end, [Chain.ETHEREUM], grid=grid)
    assert len(report.tuning) == 2
    assert all(r.end <= report.validation_chosen.start for r in report.tuning)
    assert report.validation_chosen.end == end
    assert report.validation_default.premiums == (Decimal(2), Decimal(6))
