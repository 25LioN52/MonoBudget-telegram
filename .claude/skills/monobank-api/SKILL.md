---
name: monobank-api
description: Monobank personal API facts for MonoBudget-telegram — endpoints, rate limits, statement range and pagination, transaction fields, holds, and how to build synthetic fixtures safely. Use when changing mono.py, sync.py, classify.py or test fixtures.
---

# Monobank personal API (as used by MonoBudget)

Docs: https://api.monobank.ua/docs/ — never call the live API from the agent (the guard hook blocks it); use `FakeMono` in `tests/conftest.py`, which enforces the same limits.

## Endpoints

- `GET /personal/client-info` → `{clientId, name, webHookUrl, permissions, accounts: [...], jars: [...]}`; account: `id`, `sendId`, `balance`, `creditLimit`, `type`, `currencyCode` (978 EUR, 980 UAH, 840 USD), `cashbackType`, `maskedPan[]`, `iban`.
- `GET /personal/statement/{account}/{from}/{to}` (Unix **seconds**) → items newest first: `id`, `time`, `description`, `mcc`, `originalMcc`, `hold`, `amount` (account currency, minor units, negative = spending), `operationAmount`, `currencyCode`, `commissionRate`, `cashbackAmount`, `balance`, `comment`, `receiptId`, `invoiceId`, `counterEdrpou`, `counterIban`, `counterName`.
- Header `X-Token`. Read-only token. Webhooks are deliberately not used (they'd need an internet-exposed endpoint, and Monobank doesn't sign them).

## Limits

- **1 request per 60 s** for all endpoints (treat as global per token) → `RATE_LIMIT = 61`, persisted `last_request_at`.
- Statement range **≤ 31 days + 1 hour** → we request ≤ 31 days (`MAX_RANGE`). October in Europe is exactly 31 days + 1 hour.
- **≤ 500 items** per response: if exactly 500, request again with `to` = oldest item time; dedupe by id (`pending`).
- 429 = rate limited; 401/403 = bad/revoked token. Always keep the cache.

## Semantics

- Count only EUR accounts and their `amount`. Own transfers: `counterIban` among the user's IBANs (all currencies, from client-info), else description patterns.
- Holds count; they can change amount or disappear → upsert by id **and** delete ids missing from a fetched window (only within the page-covered window).
- Positive amounts are refunds only for purchase categories (not cash/transfers).

## Synthetic fixtures

- IBANs `UA00…`/`ES00…` (check digits 00 are never valid), masked cards `000000******0001`, ids like `fakeTx0001`, generic names. Never copy a real statement, even "anonymised".
- Compute timestamps from ISO strings and note the Europe/Madrid local time in a comment.
