---
name: secure-commit
description: Commit changes in MonoBudget-telegram with the mandatory double security check — tests, lint, secret scan, then an independent security-reviewer pass — before anything is committed or pushed. Use whenever the user asks to commit, push, open a PR, release or "ship" changes in this repo.
---

# Secure commit

Security is the top priority here; every commit is checked twice. Follow the steps in order and stop at the first failure.

1. **Branch.** If on `main`, create a branch (`git switch -c <type>/<short-name>`). `main` only accepts PRs.
2. **Review the change set.** `git status`, `git diff`, `git diff --cached`. Nothing unrelated, generated or personal; never stage `.env`, `secrets/`, `data/`, databases, backups or `.claude/settings.local.json`.
3. **Automated check #1.** `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `python3 scripts/secret_scan.py --all`. If dependencies changed: `uv lock --check` and `uv run pip-audit` (see CI). If Docker files changed: `docker build` and the smoke checks from CI. Fix failures; never weaken a test or add `secret-scan:allow` without written justification.
4. **Independent check #2.** Launch the `security-reviewer` subagent on the change set. Fix every CRITICAL/HIGH/MEDIUM finding, repeat step 3, re-run the reviewer until `APPROVE` (or only LOW nits you report to the user).
5. **Commit.** Stage specific paths (hooks with `git add --chmod=+x .githooks/*`). Write the message to a scratchpad file and run `git commit -F <file>` as a standalone command (the guard rejects `&`, `;`, `|`, `<`, `>`, `$`, backticks in commit/push commands). Never `--no-verify`.
6. **Push & PR** (only when asked). `git push -u origin <branch>` standalone, then `gh pr create` with the template's security checklist filled in. CI must be green before merge.
7. **Release** (only when asked): tag `vX.Y.Z` on `main`; `release.yml` builds, pushes and attests the image. Verify with `gh attestation verify oci://ghcr.io/25lion52/monobudget-telegram:X.Y.Z --owner 25LioN52`.
8. **Report**: what changed, test/lint/scan results, reviewer verdict and any remaining nits.
