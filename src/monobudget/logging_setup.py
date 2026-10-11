"""Logging that never prints secrets or full IBANs.

The formatter redacts the *final* text (message, arguments and tracebacks),
so libraries that put the bot token into URLs can't leak it either.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterable

from .classify import mask_iban

_BOT_TOKEN = re.compile(r"\b\d{5,15}:[A-Za-z0-9_-]{30,64}\b")
_BOT_URL = re.compile(r"(/bot)[^/\s]+")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: Iterable[str], fmt: str) -> None:
        super().__init__(fmt)
        self.secrets = sorted({s for s in secrets if s and len(s) >= 8}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "***")
        text = _BOT_TOKEN.sub("***", text)
        text = _BOT_URL.sub(r"\1***", text)
        return _IBAN.sub(lambda m: mask_iban(m.group(0)), text)

    def format(self, record: logging.LogRecord) -> str:
        return self.redact(super().format(record))


def setup_logging(level: str, secrets: Iterable[str]) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingFormatter(secrets, "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Third-party loggers stay quiet even in DEBUG (request details, update payloads).
    for name in ("aiogram", "aiohttp", "asyncio"):
        logging.getLogger(name).setLevel(max(logging.INFO, logging.getLevelName(level)))
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.captureWarnings(True)

    def excepthook(kind, value, tb):  # uncaught errors also go through the redactor
        logging.getLogger("monobudget").critical("Uncaught exception", exc_info=(kind, value, tb))

    sys.excepthook = excepthook
