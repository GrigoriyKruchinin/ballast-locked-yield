import os
from decimal import Decimal

import pytest

from ballast.config.settings import (
    AllocationSettings,
    ChainSettings,
    DatabaseSettings,
    RiskSettings,
    Role,
    settings_for_role,
)
from ballast.domain.models import Chain

ENV_PREFIXES = ("DB_", "APP_", "TELEGRAM_", "RPC_URL_", "CHAIN_", "REDIS_")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    """Tests must not depend on the caller's environment (CI sets DB_URL_OVERRIDE) or a local .env."""
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith(ENV_PREFIXES):
            monkeypatch.delenv(name)


def test_chain_list_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAIN_ENABLED", "arbitrum, base")
    assert ChainSettings().chain_enabled == (Chain.ARBITRUM, Chain.BASE)
    monkeypatch.setenv("CHAIN_ENABLED", "base")
    assert ChainSettings().chain_enabled == (Chain.BASE,)


def test_unknown_chain_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAIN_ENABLED", "solana")
    with pytest.raises(ValueError, match="unsupported chain"):
        ChainSettings()


def test_defaults_match_decisions() -> None:
    alloc = AllocationSettings()
    assert alloc.paper_capital_usd == Decimal(10_000)
    assert alloc.max_protocol_pct == Decimal(35)
    assert alloc.max_tier_b_pct == Decimal(25)
    assert alloc.max_tier_b_asset_pct == Decimal(15)
    assert alloc.max_maturity_days == 183
    risk = RiskSettings()
    assert risk.tier_premium_frac("A") == Decimal("0.02")
    assert risk.tier_premium_frac("B") == Decimal("0.06")
    with pytest.raises(ValueError):
        risk.tier_premium_frac("C")


def test_db_url_and_secret_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PASSWORD", "hunter2-secret")
    db = DatabaseSettings()
    assert "hunter2-secret" in db.url()
    assert "hunter2-secret" not in repr(db)


def test_roles_get_only_needed_groups() -> None:
    notifier = settings_for_role(Role.NOTIFIER)
    assert notifier.telegram is not None
    assert notifier.chains is None
    collector = settings_for_role(Role.COLLECTOR)
    assert collector.telegram is None
    assert collector.chains is not None
    migrations = settings_for_role(Role.MIGRATIONS)
    assert migrations.db is not None
    assert migrations.redis is None
