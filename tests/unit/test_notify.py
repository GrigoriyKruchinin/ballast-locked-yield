import json
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from ballast.domain.models import Candidate, MonitorEvent, RiskScore, Severity, Tier
from ballast.nly.calculator import NlyCalculator
from ballast.notify.formatting import OpportunityView, format_daily, format_events, format_opportunities
from ballast.notify.telegram import TelegramClient, TelegramError, split_message
from ballast.pipeline.run import BookSummary
from tests.factories import NOW, ZERO_COSTS, FixedCostModel, pt

TOKEN = "123456789:AAHsecretsecretsecretsecretsecret123"


def test_split_message_on_lines() -> None:
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(200))
    parts = split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "\n".join(parts) == text


async def test_send_html_and_no_token_leak() -> None:
    seen: list[dict[str, object]] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    client = TelegramClient(TOKEN, "42", transport=httpx.MockTransport(ok))
    assert await client.send("<b>hi</b>") == 1
    assert seen[0]["parse_mode"] == "HTML"
    assert seen[0]["chat_id"] == "42"

    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"Unauthorized for {request.url}")

    bad = TelegramClient(TOKEN, "42", transport=httpx.MockTransport(fail))
    with pytest.raises(TelegramError) as info:
        await bad.send("x")
    assert "AAHsecret" not in str(info.value)


async def test_disabled_without_token() -> None:
    assert await TelegramClient("", "").send("x") == 0


def candidate() -> Candidate:
    opp = replace(pt(asset="sUSDS<script>", days=120), quoted_apy=Decimal("0.09"))
    nly = NlyCalculator(FixedCostModel(ZERO_COSTS)).evaluate(opp, Decimal(3500), NOW)
    return Candidate(opp, nly, RiskScore(Tier.A, Decimal(19), (), True), Decimal("0.07"), frozenset())


def test_opportunities_escaped_and_explained() -> None:
    text = format_opportunities([OpportunityView.from_candidate(candidate())], Decimal("0.05"))
    assert "&lt;script&gt;" in text
    assert "<script>" not in text
    assert "NLY" in text
    assert "5.00%" in text


def test_daily_and_events() -> None:
    summary = BookSummary("all", Decimal(10050), Decimal(3500), Decimal(6550), Decimal(0), Decimal("0.05"))
    text = format_daily([(summary, Decimal("0.005"), Decimal("0.0002"))], [("PT-sUSDS", NOW)], NOW)
    assert "Все сети" in text
    assert "0.500%" in text
    events = format_events(
        [MonitorEvent("depeg", Severity.CRITICAL, "USDE", "price 0.9700", Decimal("0.97"), NOW)]
    )
    assert "🚨" in events
    assert "$1" in events  # plain-language explanation attached
