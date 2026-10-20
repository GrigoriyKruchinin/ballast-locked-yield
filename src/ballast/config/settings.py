"""Modular settings groups.

Each container role builds only the groups it needs (see :func:`settings_for_role`),
so secrets are never loaded where they are not used. Percent values in the
environment are in percent units (``35`` means 35%); use the ``*_frac`` helpers.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from ballast.domain.models import Chain
from ballast.domain.money import pct

_ENV_FILE = ".env"


def _cfg(prefix: str = "") -> SettingsConfigDict:
    return SettingsConfigDict(
        env_prefix=prefix,
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )


class AppSettings(BaseSettings):
    model_config = _cfg("APP_")

    env: str = "local"
    log_level: str = "INFO"
    whitelist_path: Path = Path("config/whitelist.yaml")
    incidents_path: Path = Path("config/incidents.yaml")


class DatabaseSettings(BaseSettings):
    model_config = _cfg("DB_")

    host: str = "localhost"
    port: int = 5432
    name: str = "ballast"
    user: str = "ballast"
    password: SecretStr = SecretStr("")
    url_override: SecretStr | None = Field(default=None, description="Full SQLAlchemy URL, e.g. for tests")

    def url(self) -> str:
        if self.url_override is not None:
            return self.url_override.get_secret_value()
        pwd = self.password.get_secret_value()
        return f"postgresql+asyncpg://{self.user}:{pwd}@{self.host}:{self.port}/{self.name}"


class RedisSettings(BaseSettings):
    model_config = _cfg()

    redis_url: str = "redis://localhost:6379/0"


class ChainSettings(BaseSettings):
    model_config = _cfg()

    chain_enabled: Annotated[tuple[Chain, ...], NoDecode] = (Chain.ARBITRUM, Chain.BASE)
    rpc_url_arbitrum: SecretStr = SecretStr("")
    rpc_url_base: SecretStr = SecretStr("")
    rpc_timeout_seconds: float = 20.0

    @field_validator("chain_enabled", mode="before")
    @classmethod
    def _parse_chains(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(Chain.from_slug(part) for part in value.split(",") if part.strip())
        return value

    def rpc_url(self, chain: Chain) -> SecretStr:
        urls = {Chain.ARBITRUM: self.rpc_url_arbitrum, Chain.BASE: self.rpc_url_base}
        return urls[chain]


class DataSourceSettings(BaseSettings):
    model_config = _cfg()

    snapshot_interval_minutes: int = 15
    pendle_api_url: str = "https://api-v2.pendle.finance/core"
    defillama_yields_url: str = "https://yields.llama.fi"
    defillama_api_url: str = "https://api.llama.fi"
    defillama_coins_url: str = "https://coins.llama.fi"
    http_timeout_seconds: float = 20.0
    http_max_retries: int = 3


class CostSettings(BaseSettings):
    model_config = _cfg()

    max_gas_pct_of_position: Decimal = Decimal("0.3")
    # Fallbacks used only when a live quote is unavailable; conservative on purpose.
    default_slippage_bps: Decimal = Decimal(10)
    default_swap_fee_bps: Decimal = Decimal(5)
    entry_gas_units: int = 900_000
    exit_gas_units: int = 700_000

    @property
    def max_gas_frac(self) -> Decimal:
        return pct(self.max_gas_pct_of_position)


class RiskSettings(BaseSettings):
    model_config = _cfg()

    tier_a_premium_pp: Decimal = Decimal(2)
    tier_b_premium_pp: Decimal = Decimal(6)
    depeg_warn_pct: Decimal = Decimal("0.5")
    depeg_critical_pct: Decimal = Decimal(2)
    tvl_drop_warn_pct: Decimal = Decimal(20)
    tvl_drop_critical_pct: Decimal = Decimal(40)
    rate_spike_pp: Decimal = Decimal(10)
    min_tvl_usd: Decimal = Decimal(5_000_000)
    min_market_liquidity_usd: Decimal = Decimal(500_000)

    def tier_premium_frac(self, tier: str) -> Decimal:
        premiums = {"A": self.tier_a_premium_pp, "B": self.tier_b_premium_pp}
        if tier not in premiums:
            msg = f"tier {tier} is not allowed"
            raise ValueError(msg)
        return pct(premiums[tier])


class AllocationSettings(BaseSettings):
    model_config = _cfg()

    paper_capital_usd: Decimal = Decimal(10_000)
    reserve_pct: Decimal = Decimal(5)
    max_protocol_pct: Decimal = Decimal(35)
    max_venue_pct: Decimal = Decimal(70)
    max_tier_b_pct: Decimal = Decimal(25)
    max_tier_b_asset_pct: Decimal = Decimal(15)
    max_maturity_days: int = 183
    min_maturity_days: int = 7
    max_liquidity_share_pct: Decimal = Decimal(2)


class ForkSettings(BaseSettings):
    model_config = _cfg()

    fork_tolerance_pct: Decimal = Decimal("0.3")
    anvil_bin: str = "anvil"
    anvil_host: str = "127.0.0.1"
    anvil_port: int = 8545


class TelegramSettings(BaseSettings):
    model_config = _cfg("TELEGRAM_")

    bot_token: SecretStr = SecretStr("")
    chat_id: str = ""
    enabled: bool = True
    api_url: str = "https://api.telegram.org"


class Role(StrEnum):
    MIGRATIONS = "migrations"
    BEAT = "beat"
    COLLECTOR = "collector"
    ANALYTICS = "analytics"
    NOTIFIER = "notifier"
    SIMULATOR = "simulator"
    CLI = "cli"


class RoleSettings(BaseModel):
    """Settings bundle for one role. Groups a role does not need stay ``None``."""

    model_config = {"frozen": True, "arbitrary_types_allowed": True}

    app: AppSettings
    db: DatabaseSettings | None = None
    redis: RedisSettings | None = None
    chains: ChainSettings | None = None
    data: DataSourceSettings | None = None
    costs: CostSettings | None = None
    risk: RiskSettings | None = None
    allocation: AllocationSettings | None = None
    fork: ForkSettings | None = None
    telegram: TelegramSettings | None = None


_ROLE_GROUPS: dict[Role, frozenset[str]] = {
    Role.MIGRATIONS: frozenset({"db"}),
    Role.BEAT: frozenset({"redis", "data"}),
    Role.COLLECTOR: frozenset({"db", "redis", "chains", "data", "risk"}),
    Role.ANALYTICS: frozenset({"db", "redis", "chains", "data", "costs", "risk", "allocation"}),
    Role.NOTIFIER: frozenset({"db", "redis", "telegram"}),
    Role.SIMULATOR: frozenset({"db", "redis", "chains", "costs", "fork"}),
    Role.CLI: frozenset({"db", "redis", "chains", "data", "costs", "risk", "allocation", "fork", "telegram"}),
}

_FACTORIES: dict[str, type[BaseSettings]] = {
    "db": DatabaseSettings,
    "redis": RedisSettings,
    "chains": ChainSettings,
    "data": DataSourceSettings,
    "costs": CostSettings,
    "risk": RiskSettings,
    "allocation": AllocationSettings,
    "fork": ForkSettings,
    "telegram": TelegramSettings,
}


def settings_for_role(role: Role) -> RoleSettings:
    """Build only the settings groups a role needs."""
    groups = {name: _FACTORIES[name]() for name in _ROLE_GROUPS[role]}
    return RoleSettings(app=AppSettings(), **groups)
