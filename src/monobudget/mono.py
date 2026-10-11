"""Monobank personal API client (read-only).

Only https://api.monobank.ua is contacted, the token is sent only in the
X-Token header and redirects are never followed (they would carry the header
to another host). Errors carry a kind and HTTP status, never response bodies.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

import aiohttp

from .config import ACCOUNT_ID_RE

MONO_API = "https://api.monobank.ua"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class MonoError(Exception):
    def __init__(self, kind: str, status: int = 0, detail: str = "") -> None:
        super().__init__(f"Monobank request failed: {kind} (HTTP {status}){' ' + detail if detail else ''}")
        self.kind = kind  # rate_limit | auth | network | http | parse
        self.status = status


class MonoApi(Protocol):
    async def client_info(self) -> dict[str, Any]: ...
    async def statement(self, account_id: str, start: int, end: int) -> list[Any]: ...


def statement_path(account_id: str, start: int, end: int) -> str:
    if not ACCOUNT_ID_RE.match(account_id):
        raise ValueError("invalid account id")
    return f"/personal/statement/{account_id}/{int(start)}/{int(end)}"


def error_kind(status: int) -> str:
    if status == 429:
        return "rate_limit"
    if status in (401, 403):
        return "auth"
    return "http"


class MonoClient:
    def __init__(self, token: str, session: aiohttp.ClientSession) -> None:
        self._token = token
        self._session = session

    def __repr__(self) -> str:
        return "MonoClient(token=***)"

    async def client_info(self) -> dict[str, Any]:
        data = await self._get("/personal/client-info")
        if not isinstance(data, dict):
            raise MonoError("parse", 200)
        return data

    async def statement(self, account_id: str, start: int, end: int) -> list[Any]:
        data = await self._get(statement_path(account_id, start, end))
        if not isinstance(data, list):
            raise MonoError("parse", 200)
        return data

    async def _get(self, path: str) -> Any:
        try:
            async with self._session.get(
                MONO_API + path,
                headers={"X-Token": self._token, "Accept": "application/json"},
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise MonoError(error_kind(response.status), response.status)
                if response.content_length is not None and response.content_length > MAX_RESPONSE_BYTES:
                    raise MonoError("parse", 200, "response too large")
                # Read the whole body (it arrives in pieces over TLS), capped while reading.
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise MonoError("parse", 200, "response too large")
                    chunks.append(chunk)
                body = b"".join(chunks)
        except MonoError:
            raise
        except (aiohttp.ClientError, TimeoutError) as error:
            # Only the error type: messages may contain URLs (with account ids).
            raise MonoError("network", detail=type(error).__name__) from None
        try:
            return json.loads(body)
        except ValueError:
            raise MonoError("parse", 200) from None
