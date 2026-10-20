"""Telegram Bot API client (HTML parse mode). The bot token never appears in logs or errors."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ballast.logging import get_logger, redact_text

log = get_logger(__name__)

MAX_MESSAGE = 4000  # Telegram limit is 4096; keep headroom for entities


class TelegramError(RuntimeError):
    pass


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split on line boundaries so HTML tags opened on a line are closed on the same line."""
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            parts.append(current)
            current = line[:limit]
        else:
            current = candidate[:limit]
    if current:
        parts.append(current)
    return parts


@dataclass(slots=True)
class TelegramClient:
    token: str
    chat_id: str
    api_url: str = "https://api.telegram.org"
    timeout_seconds: float = 15.0
    transport: httpx.AsyncBaseTransport | None = None

    async def send(self, text: str, *, silent: bool = False) -> int:
        if not self.token or not self.chat_id:
            log.info("telegram_disabled")
            return 0
        sent = 0
        async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
            for chunk in split_message(text):
                payload = {
                    "chat_id": self.chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "disable_notification": silent,
                }
                try:
                    response = await client.post(f"{self.api_url}/bot{self.token}/sendMessage", json=payload)
                except httpx.HTTPError as exc:
                    raise TelegramError(redact_text(f"telegram send failed: {exc}")) from None
                if response.status_code != 200:
                    body = redact_text(response.text[:300])
                    raise TelegramError(f"telegram HTTP {response.status_code}: {body}")
                sent += 1
        return sent
