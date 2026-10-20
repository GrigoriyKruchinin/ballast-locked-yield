from ballast.logging import REDACTED, redact, redact_text


def test_sensitive_keys_masked() -> None:
    event = {"event": "x", "private_key": "abc", "api_key": "k", "db_password": "p", "ok": "fine"}
    out = redact(None, "info", dict(event))
    assert out["private_key"] == REDACTED
    assert out["api_key"] == REDACTED
    assert out["db_password"] == REDACTED
    assert out["ok"] == "fine"


def test_secret_patterns_in_text() -> None:
    pk = "0x" + "ab" * 32
    rpc = "https://arb-mainnet.g.alchemy.com/v2/AbCdEf1234567890XyZ"
    tg = "https://api.telegram.org/bot123456789:AAHfakefakefakefakefakefakefake12345/sendMessage"
    text = redact_text(f"key {pk} rpc {rpc} tg {tg}")
    assert "ab" * 32 not in text
    assert "AbCdEf1234567890XyZ" not in text
    assert "AAHfakefakefakefakefakefakefake12345" not in text


def test_nested_values() -> None:
    out = redact(None, "info", {"event": "x", "ctx": {"token": "t", "url": "https://h/v2/SECRETSECRET"}})
    assert out["ctx"]["token"] == REDACTED
    assert "SECRETSECRET" not in out["ctx"]["url"]


def test_addresses_are_not_redacted() -> None:
    addr = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    assert redact_text(addr) == addr
