"""The Claude Code guard hook must keep blocking risky commands (exit 2 = blocked)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from .conftest import ROOT

HOOK = ROOT / ".claude" / "hooks" / "guard_bash.py"
MONO = ".".join(["api", "monobank", "ua"])
TG = ".".join(["api", "telegram", "org"])
HOOKS_PATH = ".".join(["core", "hooksPath"])


def run_hook(command: str | None, cwd=ROOT, raw: str | None = None) -> subprocess.CompletedProcess:
    payload = raw if raw is not None else json.dumps({"tool_input": {"command": command}, "cwd": str(cwd)})
    return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, check=False)


BLOCKED = [
    f'curl -H "X-Token: x" https://{MONO}/personal/client-info',
    f"Invoke-RestMethod https://{MONO}/personal/client-info",
    f"curl https://{TG}/bot123/getMe",
    f"python -c \"import urllib.request; urllib.request.urlopen('https://{TG}')\"",
    'git commit --no-verify -m "x"',
    'git commit --no-verif -m "x"',
    'git commit "--no-verify" -m x',
    "git commit '-n' -m x",
    "git commit \\--no-verify -m x",
    'git commit -n -m "x"',
    f'git -c {HOOKS_PATH}=/dev/null commit -m "x"',
    f"git config {HOOKS_PATH} /dev/null",
    "git -c include.path=/tmp/x.cfg commit -m x",
    "git --config-env=x=Y commit -m x",
    "printf x >> .git/config",
    "echo 'exit 0' > .githooks/pre-commit && git commit -m x",
    "GIT_CONFIG_COUNT=1 git commit -m x",
    "git add -f .env",
    "git stage -f .env",
    "git add --forc secrets/mono_token.txt",
    "git update-index --add .env",
    "git push --force origin main",
    "git push --force-with-lease",
    "git push origin +main",
    'git push origin "+refs/heads/main"',
    "git push --mirror",
    "git push --prune origin",
    "git push origin --delete main",
    "git push origin :main",
    "git -c remote.origin.mirror=true push",
    "git --work-tree . push --no-${a}verify origin main",
    "git commit -am x && git push",
    'git commit -m "$(cat msg)"',
    "git commit -F msg; echo done",
    "cp x README.md & git commit -am x",
    "git -c alias.c=commit c -n -m x",
    "git -c ALIAS.c=commit c -n -m x",
    "git config alias.ci commit",
    "git commit-tree HEAD^{tree} -m x",
    "git update-ref refs/heads/main abc123",
    "git hash-object -w secrets.txt",
]

ALLOWED = [
    f"grep -n {MONO} README.md",
    "git status",
    "git diff --cached",
    "git log --oneline",
    f"git config {HOOKS_PATH} .githooks",
    "git add --chmod=+x .githooks/pre-commit .githooks/commit-msg .githooks/pre-push",
    "uv run pytest",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_blocked(command):
    r = run_hook(command)
    assert r.returncode == 2, (command, r.stderr)


@pytest.mark.parametrize("command", ALLOWED)
def test_allowed(command):
    r = run_hook(command)
    assert r.returncode == 0, (command, r.stderr)


def test_fails_closed_on_malformed_input():
    assert run_hook(None, raw="{not json").returncode == 2


@pytest.mark.skipif(shutil.which("git") is None, reason="git required")
def test_commit_requires_enabled_and_untampered_hooks(tmp_path):
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "secret_scan.py", tmp_path / "scripts" / "secret_scan.py")

    r = run_hook("git commit -F msg.txt", cwd=tmp_path)
    assert r.returncode == 2 and "not enabled" in r.stderr

    git("config", HOOKS_PATH, ".githooks")
    (tmp_path / ".githooks").mkdir()
    for hook in ("pre-commit", "commit-msg", "pre-push"):
        shutil.copy(ROOT / ".githooks" / hook, tmp_path / ".githooks" / hook)
    git("add", ".githooks")
    (tmp_path / ".githooks" / "pre-commit").write_text(
        "#!/bin/sh\n# scripts/secret_scan.py secret-scan: OK\nexit 0\n", encoding="utf-8"
    )
    r = run_hook("git commit -F msg.txt", cwd=tmp_path)
    assert r.returncode == 2 and "differs from its staged version" in r.stderr

    (tmp_path / ".githooks" / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    r = run_hook("git commit -F msg.txt", cwd=tmp_path)
    assert r.returncode == 2 and "no longer runs the secret scanner" in r.stderr


def test_git_dash_c_targets_the_other_repository(tmp_path):
    r = run_hook(f"git -C {tmp_path} commit -F msg.txt")
    assert r.returncode == 2  # not a repo with our hooks/scanner: refuse rather than scan the wrong one
