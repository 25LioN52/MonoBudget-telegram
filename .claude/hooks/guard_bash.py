#!/usr/bin/env python3
"""Claude Code PreToolUse hook for Bash/PowerShell commands.

A tripwire, not a sandbox: it stops the common ways an agent could leak data or
skip checks. Exit code 2 blocks the command (stderr is shown to Claude); any
internal error also blocks (fail closed).

- Blocks direct calls to the Monobank API and the Telegram Bot API: the agent
  must never use the real tokens.
- Blocks skipping or redirecting git hooks/config, force-staging ignored files,
  force/mirror/prune pushes and remote deletions.
- `git commit` / `git push` must be standalone commands (no chaining, pipes,
  redirects, substitutions or `$`), so the checks see the final state.
- Before a commit: hooks must be enabled and match their staged versions; the
  staged files and the working tree are scanned. Before a push: the working tree
  and every commit not yet on that remote are scanned.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

HOOKS = ("pre-commit", "commit-msg", "pre-push")
SEGMENT = r"[^\n|;&]*"


def block(message: str) -> None:
    sys.stderr.write(f"Blocked by .claude/hooks/guard_bash.py: {message}\n")
    sys.exit(2)


def git_out(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return ""


def target_repo(command: str, default: Path) -> Path:
    """`git -C <dir> …` targets another directory: check and scan that repository."""
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        words = command.split()
    for i, word in enumerate(words[:-1]):
        if word == "-C":
            path = Path(words[i + 1])
            return path if path.is_absolute() else default / path
    return default


def scan(repo: Path, *args: str) -> None:
    scanner = repo / "scripts" / "secret_scan.py"
    if not scanner.exists():
        block(f"{repo} has no scripts/secret_scan.py — refusing to commit/push unscanned content.")
    result = subprocess.run(
        [sys.executable, str(scanner), *args], cwd=repo, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        output = result.stderr or result.stdout
        block(f"secret scan ({' '.join(args)}) failed — nothing was committed or pushed.\n{output}")
    if not re.search(r"^secret-scan: OK", result.stdout, re.M):
        block(f"secret scan ({' '.join(args)}) did not confirm OK — failing closed.")


def check_git_hooks(repo: Path) -> None:
    if git_out(repo, "config", "--get", "core.hooksPath") != ".githooks":
        block("git hooks are not enabled. Run: git config core.hooksPath .githooks")
    for hook in HOOKS:
        path = repo / ".githooks" / hook
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            block(f".githooks/{hook} is missing.")
        if "scripts/secret_scan.py" not in text or "secret-scan: OK" not in text:
            block(f".githooks/{hook} no longer runs the secret scanner.")
        # Git runs the working-tree hook: it must equal the staged version, so changes are reviewed.
        staged = git_out(repo, "ls-files", "-s", "--", f".githooks/{hook}").split()
        if len(staged) > 1 and git_out(repo, "hash-object", "--", f".githooks/{hook}") != staged[1]:
            block(f".githooks/{hook} differs from its staged version — stage and review hook changes first.")


def push_remote(command: str) -> str:
    rest = re.split(r"\bpush\b", command, maxsplit=1)[-1].split()
    remote = next((a for a in rest if not a.startswith("-")), "origin")
    return remote if re.fullmatch(r"[\w.-]+", remote) else "origin"


def run(payload: dict) -> None:
    raw = str((payload.get("tool_input") or {}).get("command") or "")
    default_repo = Path(payload.get("cwd") or ".").resolve()
    # Quotes, backslashes and backticks must not hide flags: "--no-verify", '-n', \--force.
    command = re.sub(r"[\"'\\`]", "", raw)

    network = re.compile(
        r"\b(curl|wget|Invoke-WebRequest|Invoke-RestMethod|iwr|irm|http|https|fetch|node|python3?|py|"
        r"deno|bun|ruby|perl|nc|openssl)\b",
        re.I,
    )
    if re.search(r"api\.monobank\.ua|api\.telegram\.org", command, re.I) and network.search(command):
        block(
            "direct requests to api.monobank.ua / api.telegram.org are not allowed from the agent. "
            "Use the fakes in tests/conftest.py."
        )

    if not re.search(r"\bgit\b", command):
        return

    def sub(name: str) -> bool:
        return re.search(rf"\bgit\b{SEGMENT}(?<![\w./=:-]){name}(?![\w./-])", command) is not None

    git_commit, git_push = sub("commit"), sub("push")

    if re.search(r"(^|\s)--no-ver\w*", command):
        block("--no-verify (or an abbreviation) skips the secret scan.")
    enabling = re.fullmatch(r"\s*git config (--local )?core\.hooksPath \.githooks\s*", command) is not None
    if not enabling and re.search(
        r"core\.hooksPath|GIT_CONFIG_\w+|\bHUSKY=0\b|include\.path|includeIf|--config-env|"
        r"\.git/config|\.git/hooks",
        command,
        re.I,
    ):
        block("changing hook paths or injecting git config would bypass the secret scan.")
    if re.search(r"remote\.[^\s=]*\.(mirror|push)\b", command, re.I):
        block("changing remote push/mirror config is not allowed.")
    if re.search(rf"\bgit\b{SEGMENT}\b(add|stage)\b{SEGMENT}(\s-[a-zA-Z]*f[a-zA-Z]*\b|\s--fo\w*)", command):
        block("`git add -f` would stage ignored files (secrets, database, exports).")
    if re.search(
        r"-c\s*alias\.|\balias\.\w+\s*=|\bconfig\b[^\n]*\balias\.|\bcommit-tree\b|\bupdate-ref\b|"
        r"\bhash-object\b[^\n]*\s-w\b",
        command,
        re.I,  # git config keys are case-insensitive (ALIAS.c=commit)
    ):
        block("git aliases, commit-tree, update-ref and hash-object -w can commit without the checks.")
    if re.search(r"\bupdate-index\b[^\n]*--add\b", command):
        block("`git update-index --add` would stage ignored files.")
    if git_commit and re.search(rf"\bcommit\b{SEGMENT}\s-[a-zA-Z]*n[a-zA-Z]*\b", command):
        block("`git commit -n` skips the secret scan.")
    if git_push and re.search(
        r"(\s--fo\w*|\s-[a-zA-Z]*f\b|[\s=]\+[\w/.:-]|\s--mirror\b|\s--delete\b|\s-d\b|"
        r"\s--prune\b|\s:[\w/.-])",
        command,
    ):
        block("force, mirror and prune pushes and remote deletions are not allowed.")

    if git_commit or git_push:
        if re.search(r"[&;|\n`<>$]", raw):
            block(
                "run `git commit` / `git push` as a standalone command (no &, &&, ;, |, redirects, $ or backticks). "
                "For the message use `git commit -F <file>`."
            )
        repo = target_repo(raw, default_repo)
        check_git_hooks(repo)
        if git_commit:
            scan(repo, "--staged")
            scan(repo, "--all")
        if git_push:
            scan(repo, "--all")
            scan(repo, "--history", "HEAD", "--not", f"--remotes={push_remote(command)}")


if __name__ == "__main__":
    try:
        run(json.loads(sys.stdin.read() or "{}"))
    except SystemExit:
        raise
    except Exception as error:
        block(f"guard hook error (failing closed): {type(error).__name__}: {error}")
    sys.exit(0)
