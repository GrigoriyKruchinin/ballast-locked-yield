"""Structured logging with secret redaction.

Every log event passes through :func:`redact` which masks values of sensitive keys
and anything that looks like a secret inside strings (RPC URLs with keys, bot tokens,
private keys).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

_SENSITIVE_KEYS = re.compile(r"(key|secret|token|password|passwd|mnemonic|seed|private|auth)", re.IGNORECASE)
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"0x[0-9a-fA-F]{64}"), "0x<redacted-64>"),
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"), "<redacted-bot-token>"),
    (re.compile(r"(/v2/|/v3/|apikey=|api_key=|key=)[A-Za-z0-9_-]{8,}"), r"\1<redacted>"),
    (re.compile(r"(bot)[0-9]{6,12}:[A-Za-z0-9_-]+"), r"\1<redacted>"),
)
REDACTED = "<redacted>"


def redact_text(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _redact_value(key: str, value: object) -> object:
    if _SENSITIVE_KEYS.search(key) and value not in (None, ""):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {k: _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(_redact_value(key, v) for v in value)
    return value


def redact(
    _logger: object,
    _method: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        event_dict[key] = _redact_value(key, event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO", *, json: bool = False) -> None:
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            redact,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
