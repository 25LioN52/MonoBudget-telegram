# MonoBudget-telegram — guide for Claude

Self-hosted, single-user Telegram bot (Python 3.13, aiogram 3, SQLite) that tracks a weekly/monthly spending budget from Monobank EUR accounts. Runs as a hardened Docker container on the user's UGREEN NAS; the image is built by CI and published to GHCR with provenance attestations. Public repo, MIT. User docs are in Ukrainian (README, SECURITY, CONTRIBUTING); code and comments are in English. Sister project: MonoBudget-ios (Scriptable widgets) — same budget rules.

## Security is the top priority — always double-check

The bot holds two secrets (Telegram bot token, Monobank personal token) and the user's financial data. Every change gets **two independent security checks** before it lands:

1. **Automated:** `uv run pytest`, `uv run ruff check .`, `python3 scripts/secret_scan.py --all`. Git hooks in `.githooks` (pre-commit, commit-msg, pre-push) run the scanner and fail closed. The PreToolUse hook `.claude/hooks/guard_bash.py` scans before every `git commit`/`git push` (push: all unpushed commits), and blocks hook bypasses (`--no-verify` and abbreviations, `-n`, `core.hooksPath`/`GIT_CONFIG_*`/`include.path` overrides, editing `.git/config`), `git add -f`, force/mirror/prune pushes, remote deletions and direct calls to `api.monobank.ua` / `api.telegram.org`. It's a tripwire, not a sandbox — never look for ways around it. Run `git commit -F <message-file>` and `git push …` as **standalone** commands (no `&`, `;`, `|`, `<`, `>`, `$`, backticks).
2. **Review:** run the `security-reviewer` subagent on the change set and fix every finding before committing. The `/secure-commit` skill does all of this in order.

CI repeats everything (tests, ruff, pip-audit, our scanner on tree + full history, TruffleHog, Docker build + smoke test), and `main` only accepts PRs with green CI.

Hard rules:
- Never put a real token, statement, IBAN, card number, Telegram user id or name anywhere (code, tests, fixtures, commits, issues, chat output). Fixtures are synthetic: IBANs use check digits `00`, cards are masked, fake tokens are assembled at runtime in `tests/conftest.py`.
- Never contact Monobank or Telegram from the agent; use `FakeMono` / `FakeMessenger` in `tests/conftest.py`.
- Secrets come only from env or `*_FILE` (Docker secrets). Never log, print, echo in errors, or put them in URLs other than aiogram's own. Logging goes through `RedactingFormatter` (tokens, bot URLs, IBANs).
- Monobank client: only `https://api.monobank.ua`, token only in `X-Token`, `allow_redirects=False`, size-limited bodies, errors carry kind + status only.
- Telegram: every update passes `AccessMiddleware` (ALLOWED_USER_IDS, private chats only, leave groups); every bank/user string is HTML-escaped (`render.esc`); callback data is parsed by strict regexes and stays ≤ 64 bytes.
- Store only what reports need (no balances, no counterparty name/IBAN fields; own IBANs only as HMACs with a per-install key). The DB is created owner-only (umask 077). An unexpected API response — statement item *or* client-info account — must never delete data (`parse` error instead).
- Sync: one request per step; polls stretch to `(accounts + 1) × 61 s` and an overdue reconcile goes first, so history/reconcile never starve. Full pages reconcile only `(oldest, end]`. Notification state lives in the `notified` table (a re-inserted row is never announced twice).
- A credit is a refund only if its MCC (not a user override) is a purchase category; MCC 0, transfers and cash are income. Keep this identical to MonoBudget-ios.
- Releases: only tags `vX.Y.Z` on `main` matching `pyproject.toml`, after the full CI (reusable `ci.yml`); tag rulesets block moving/deleting tags. Never publish images any other way.
- Container: non-root, read-only rootfs, `cap_drop: ALL`, `no-new-privileges`, no ports, no pip at runtime, base images pinned by digest.
- Dependencies: minimal, locked with hashes in `uv.lock` (adding one needs the user's approval: `uv add` is in `ask`). No ad-hoc `pip install`. GitHub Actions pinned by commit SHA.
- Never write invisible/bidi/control characters literally: build them with `chr()`. `tests/test_repo.py` and the scanner's `hidden-unicode` rule enforce this.

## Layout

- `src/monobudget/` — `config.py` (env + `*_FILE`), `periods.py`, `categories.py`, `classify.py` (pure budget logic), `storage.py` (SQLite), `mono.py` (client), `sync.py` (planner + `Syncer`, one request per step), `render.py` (texts/keyboards, pure), `i18n.py` (uk/en, same keys), `messenger.py` (aiogram adapter), `service.py` (bot logic + loop), `bot.py` (aiogram handlers + access middleware), `logging_setup.py`, `app.py`, `__main__.py` (run | check-config | healthcheck | backup).
- `tests/` — pytest; `conftest.py` has the fakes and synthetic fixtures in `tests/fixtures/`.
- `scripts/secret_scan.py` — stdlib-only scanner: `--staged`, `--all`, `--history REVS…`, `--message FILE|-`, files. Prints `secret-scan: OK` only when clean; callers must check that line.
- `Dockerfile`, `docker-compose.yml`, `example.env`; `.github/workflows/ci.yml` (checks) and `release.yml` (GHCR publish + attestation).

## Commands

```bash
uv sync                                   # dev environment (locked)
uv run pytest                             # tests
uv run ruff check . && uv run ruff format --check .
python3 scripts/secret_scan.py --all      # scanner (also --history --all)
docker build -t monobudget-telegram:dev . # image
```

## Monobank API constraints (must hold in code and tests)

- `GET /personal/client-info`, `GET /personal/statement/{account}/{from}/{to}` (Unix seconds), header `X-Token`.
- 1 request per 60 s for all endpoints → `RATE_LIMIT`, persisted `last_request_at` (survives restarts).
- Statement range ≤ 31 days + 1 hour → we request ≤ 31 days. ≤ 500 items → page backwards (`pending`).
- Count only EUR accounts (978), use `amount`, include holds; every fetched window is reconciled (cancelled holds deleted).

## Conventions

- Keep budget logic pure and tested; add fixtures for new edge cases (DST, month boundaries…).
- UI strings in both `uk` and `en` (a test checks parity).
- Work on a branch, open a PR; never push to `main` (ruleset: PR + green CI).
