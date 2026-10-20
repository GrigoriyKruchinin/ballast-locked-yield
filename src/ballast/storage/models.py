"""SQLAlchemy 2.0 models. Money columns are ``Numeric(38, 18)`` (exact in PostgreSQL)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Money = Numeric(38, 18)
Wei = Numeric(40, 0)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {  # Any: heterogeneous SQLAlchemy type map
        Decimal: Money,
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSON,
        list[str]: JSON,
    }


class PendleMarketSnapshotRow(Base):
    __tablename__ = "pendle_market_snapshots"
    __table_args__ = (Index("ix_pendle_snap_market_time", "chain_id", "market", "observed_at"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(index=True)
    chain_id: Mapped[int]
    market: Mapped[str] = mapped_column(String(66))
    name: Mapped[str] = mapped_column(String(64))
    protocol_name: Mapped[str] = mapped_column(String(128))
    expiry: Mapped[datetime]
    is_stable: Mapped[bool] = mapped_column(Boolean)
    is_whitelisted: Mapped[bool] = mapped_column(Boolean, default=False)
    pt: Mapped[str] = mapped_column(String(66))
    sy: Mapped[str] = mapped_column(String(66))
    underlying: Mapped[str] = mapped_column(String(66))
    accounting: Mapped[str] = mapped_column(String(66))
    implied_apy: Mapped[Decimal | None]
    underlying_apy: Mapped[Decimal | None]
    liquidity_usd: Mapped[Decimal]
    total_tvl_usd: Mapped[Decimal]
    total_pt: Mapped[Decimal]
    total_sy: Mapped[Decimal]
    fee_rate: Mapped[Decimal | None]
    pt_usd: Mapped[Decimal | None]
    accounting_usd: Mapped[Decimal | None]
    underlying_usd: Mapped[Decimal | None]
    categories: Mapped[list[str]]


class PendleHistoryRow(Base):
    """Hourly history of (mostly matured) markets for the backtest."""

    __tablename__ = "pendle_market_history"
    __table_args__ = (Index("ux_pendle_hist", "chain_id", "market", "timestamp", unique=True),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    chain_id: Mapped[int]
    market: Mapped[str] = mapped_column(String(66))
    timestamp: Mapped[datetime]
    implied_apy: Mapped[Decimal | None]
    pt_usd: Mapped[Decimal | None]
    sy_usd: Mapped[Decimal | None]
    tvl_usd: Mapped[Decimal | None]
    total_pt: Mapped[Decimal | None]
    total_sy: Mapped[Decimal | None]
    underlying_apy: Mapped[Decimal | None]
    volume_usd: Mapped[Decimal | None]


class PendleMarketMetaRow(Base):
    """Static market metadata (incl. matured markets) for the backtest universe."""

    __tablename__ = "pendle_markets"
    __table_args__ = (Index("ux_pendle_market", "chain_id", "market", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int]
    market: Mapped[str] = mapped_column(String(66))
    name: Mapped[str] = mapped_column(String(64))
    protocol_name: Mapped[str] = mapped_column(String(128))
    expiry: Mapped[datetime]
    is_stable: Mapped[bool] = mapped_column(Boolean)
    pt: Mapped[str] = mapped_column(String(66))
    sy: Mapped[str] = mapped_column(String(66))
    underlying: Mapped[str] = mapped_column(String(66))
    accounting: Mapped[str] = mapped_column(String(66))
    fee_rate: Mapped[Decimal | None]
    history_loaded_at: Mapped[datetime | None]


class ParkingSnapshotRow(Base):
    __tablename__ = "parking_snapshots"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(index=True)
    pool_id: Mapped[str] = mapped_column(String(64))
    protocol: Mapped[str] = mapped_column(String(64))
    chain_id: Mapped[int]
    asset_symbol: Mapped[str] = mapped_column(String(32))
    apy_base: Mapped[Decimal]
    apy_reward: Mapped[Decimal | None]
    tvl_usd: Mapped[Decimal]


class BasisSnapshotRow(Base):
    __tablename__ = "basis_snapshots"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(index=True)
    venue: Mapped[str] = mapped_column(String(32))
    instrument: Mapped[str] = mapped_column(String(64))
    underlying: Mapped[str] = mapped_column(String(16))
    expiry: Mapped[datetime]
    future_price: Mapped[Decimal]
    index_price: Mapped[Decimal]
    basis_apr: Mapped[Decimal | None]


class GasSnapshotRow(Base):
    __tablename__ = "gas_snapshots"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(index=True)
    chain_id: Mapped[int]
    gas_price_wei: Mapped[Decimal] = mapped_column(Wei)
    native_usd: Mapped[Decimal]
    l1_fee_usd: Mapped[Decimal]


class PegPriceRow(Base):
    __tablename__ = "peg_prices"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    price_usd: Mapped[Decimal]


class ProtocolTvlRow(Base):
    __tablename__ = "protocol_tvl"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(index=True)
    protocol: Mapped[str] = mapped_column(String(64))
    llama_slug: Mapped[str] = mapped_column(String(64))
    tvl_usd: Mapped[Decimal]
    tvl_7d_ago_usd: Mapped[Decimal | None]


class QuoteRow(Base):
    __tablename__ = "slippage_quotes"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(index=True)
    opportunity_key: Mapped[str] = mapped_column(String(128), index=True)
    size_usd: Mapped[Decimal]
    use_limit_order: Mapped[bool] = mapped_column(Boolean)
    price_impact: Mapped[Decimal]
    effective_apy: Mapped[Decimal | None]
    fee_usd: Mapped[Decimal]
    gas_used: Mapped[int | None] = mapped_column(BigInteger)


class ScanRow(Base):
    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(16))
    parking_apy: Mapped[Decimal | None]
    n_opportunities: Mapped[int] = mapped_column(Integer, default=0)
    n_eligible: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")


class NlyResultRow(Base):
    __tablename__ = "nly_results"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    opportunity_key: Mapped[str] = mapped_column(String(128), index=True)
    chain_id: Mapped[int]
    kind: Mapped[str] = mapped_column(String(32))
    asset_symbol: Mapped[str] = mapped_column(String(64))
    protocol: Mapped[str] = mapped_column(String(64))
    maturity: Mapped[datetime | None]
    quoted_apy: Mapped[Decimal | None]
    capital_usd: Mapped[Decimal]
    days: Mapped[Decimal]
    gross_return: Mapped[Decimal]
    net_return: Mapped[Decimal]
    nly_apy: Mapped[Decimal]
    nly_monthly: Mapped[Decimal]
    gas_usd: Mapped[Decimal]
    fees_usd: Mapped[Decimal]
    slippage_usd: Mapped[Decimal]
    liquidity_usd: Mapped[Decimal]


class RiskScoreRow(Base):
    __tablename__ = "risk_scores"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    opportunity_key: Mapped[str] = mapped_column(String(128), index=True)
    tier: Mapped[str] = mapped_column(String(1))
    score: Mapped[Decimal]
    allowed: Mapped[bool] = mapped_column(Boolean)
    threshold_apy: Mapped[Decimal | None]
    findings: Mapped[dict[str, Any]]


class DecisionRow(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"), index=True)
    created_at: Mapped[datetime]
    opportunity_key: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    capital_usd: Mapped[Decimal]
    nly_apy: Mapped[Decimal | None]
    threshold_apy: Mapped[Decimal | None]


class PaperPositionRow(Base):
    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio: Mapped[str] = mapped_column(String(32), index=True)
    opportunity_key: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    chain_id: Mapped[int]
    asset_symbol: Mapped[str] = mapped_column(String(64))
    protocol: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    capital_usd: Mapped[Decimal]
    entry_price: Mapped[Decimal]
    units: Mapped[Decimal]
    expected_apy: Mapped[Decimal]
    entry_costs_usd: Mapped[Decimal]
    expected_exit_costs_usd: Mapped[Decimal]
    exposures: Mapped[list[str]]
    maturity: Mapped[datetime | None]
    opened_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]
    payoff_usd: Mapped[Decimal | None]
    close_reason: Mapped[str | None] = mapped_column(Text)


class PaperDailyRow(Base):
    __tablename__ = "paper_daily"
    __table_args__ = (Index("ux_paper_daily", "portfolio", "day", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio: Mapped[str] = mapped_column(String(32))
    day: Mapped[date] = mapped_column(Date)
    total_value_usd: Mapped[Decimal]
    locked_usd: Mapped[Decimal]
    parked_usd: Mapped[Decimal]
    idle_usd: Mapped[Decimal]
    accrued_usd: Mapped[Decimal]
    costs_paid_usd: Mapped[Decimal]
    parking_apy: Mapped[Decimal | None]
    n_positions: Mapped[int] = mapped_column(Integer)


class SimulatedTxRow(Base):
    __tablename__ = "simulated_txs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime]
    opportunity_key: Mapped[str] = mapped_column(String(128), index=True)
    chain_id: Mapped[int]
    fork_block: Mapped[int] = mapped_column(BigInteger)
    description: Mapped[str] = mapped_column(Text)
    amount_in_usd: Mapped[Decimal]
    expected_out_raw: Mapped[Decimal] = mapped_column(Wei)
    actual_out_raw: Mapped[Decimal | None] = mapped_column(Wei)
    deviation_pct: Mapped[Decimal | None]
    gas_used: Mapped[int | None] = mapped_column(BigInteger)
    success: Mapped[bool] = mapped_column(Boolean)
    within_tolerance: Mapped[bool | None] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)


class MonitorEventRow(Base):
    __tablename__ = "monitor_events"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    value: Mapped[Decimal | None]
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


class BacktestRunRow(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime]
    window: Mapped[str] = mapped_column(String(16))  # tuning | validation | full
    start: Mapped[datetime]
    end: Mapped[datetime]
    params: Mapped[dict[str, Any]]
    metrics: Mapped[dict[str, Any]]
