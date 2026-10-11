"""Tests for scripts/secret_scan.py. Secrets are generated at runtime, so this file contains none."""

from __future__ import annotations

import importlib.util
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import ROOT

SCANNER = ROOT / "scripts" / "secret_scan.py"
spec = importlib.util.spec_from_file_location("secret_scan", SCANNER)
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


def valid_iban(country: str, bban: str) -> str:
    rearranged = bban + country + "00"
    remainder = int("".join(str(int(ch, 36)) for ch in rearranged)) % 97
    return f"{country}{98 - remainder:02d}{bban}"


def luhn(prefix15: str) -> str:
    return next(prefix15 + str(d) for d in range(10) if scan.is_luhn(prefix15 + str(d)))


def token44() -> str:
    while True:  # base64url like a Monobank token; make sure it has all character classes
        t = secrets.token_urlsafe(33)
        if any(c.islower() for c in t) and any(c.isupper() for c in t) and any(c.isdigit() for c in t):
            return t


UA_IBAN = valid_iban("UA", "3052990000026007123456789")
NBSP = chr(0xA0)


def cli(*args: str, cwd: Path | None = None, stdin: str | None = None) -> tuple[int, str]:
    r = subprocess.run(
        [sys.executable, str(SCANNER), *args], cwd=cwd, input=stdin, capture_output=True, text=True, check=False
    )
    return r.returncode, r.stdout + r.stderr


def rules_in(text: str) -> set[str]:
    return {rule for _, rule, _ in scan.scan_text(text)}


@pytest.mark.parametrize(
    ("rule", "line"),
    [
        ("high-entropy-string", lambda: f'x = "{token44()}"'),
        ("token-assignment", lambda: f'MONO_TOKEN="{secrets.token_urlsafe(24)}"'),
        ("telegram-bot-token", lambda: "bot " + "1234567" + "89:" + secrets.token_urlsafe(26)[:35]),
        ("iban", lambda: f"iban: {UA_IBAN}"),
        ("card-number", lambda: "card " + luhn("537541123456789")),
        ("github-token", lambda: "gh" + "p_" + "A1b2C3d4E5" * 4),
        ("private-key", lambda: "-----BEGIN RSA " + "PRIVATE KEY-----"),
        ("hidden-unicode", lambda: "ok = 'safe" + chr(0x202E) + "'"),
    ],
)
def test_detects(rule, line):
    text = line()
    findings = scan.scan_text(text)
    assert rule in {r for _, r, _ in findings}
    assert all(len(sample) < 24 for _, _, sample in findings), "findings are masked"


def test_iban_layouts():
    grouped = " ".join(UA_IBAN[i : i + 4] for i in range(0, len(UA_IBAN), 4))
    for text in (
        f"{UA_IBAN} UAH",
        f"{UA_IBAN} 12345",
        UA_IBAN.lower(),
        grouped,
        grouped.replace(" ", NBSP),
        "-".join(UA_IBAN[i : i + 4] for i in range(0, len(UA_IBAN), 4)),
        f"pay {UA_IBAN}. thanks",
    ):
        assert "iban" in rules_in(text), text
    two = f"{valid_iban('ES', '21000418450200051332')} and {UA_IBAN}"
    assert sum(1 for _, r, _ in scan.scan_text(two) if r == "iban") == 2


def test_ignores_synthetic_data_hashes_and_allowed_rules():
    assert rules_in('"iban": "UA00' + "0" * 24 + '1"') == set()
    assert rules_in('"maskedPan": ["000000******0001"]') == set()
    assert rules_in("sha256:" + secrets.token_hex(32)) == set()
    assert rules_in('"time": 1782919800, "ms": 1782919800000') == set()
    assert rules_in("emoji " + chr(0x2764) + chr(0xFE0F) + " and Ukrainian текст") == set()
    assert rules_in(f'x = "{token44()}"  # secret-scan:allow high-entropy-string') == set()
    assert rules_in(f'"{token44()}" {UA_IBAN}  # secret-scan:allow high-entropy-string') == {"iban"}
    assert rules_in(f'x = "{token44()}"  # secret-scan:allow') != set(), "a bare marker allows nothing"
    assert rules_in("escape " + chr(92) + "u202E is text, not the character") == set()


def test_forbidden_files():
    for path in (
        ".env",
        "deploy/.env",
        "secrets/mono_token.txt",
        "data/monobudget.sqlite3",
        "backups/x.sqlite3",
        "x.db",
        "monobudget.sqlite3-wal",
        "key.pem",
        "statement.pdf",
        "export.csv",
        "shot.png",
        "CLAUDE.local.md",
        "telegram_token.txt",
    ):
        assert scan.forbidden_reason(path), path
    for path in (
        "example.env",
        "src/monobudget/storage.py",
        "docs/status.png",
        "tests/fixtures/client-info.json",
        "uv.lock",
        "docker-compose.yml",
    ):
        assert scan.forbidden_reason(path) is None, path


def test_decode():
    utf16 = b"\xff\xfe" + f'token: "{token44()}"'.encode("utf-16-le")
    assert scan.scan_text(scan.decode(utf16))
    assert scan.decode(b"PNG\x00\x01") is None


def test_cli_ok_line_and_failures(tmp_path):
    (tmp_path / "leaky.py").write_text(f'x = "{token44()}"\n', encoding="utf-8")
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "data.sqlite3").write_bytes(b"SQLite format 3\x00")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
    code, out = cli(str(tmp_path / "leaky.py"))
    assert code == 1 and "secret-scan: OK" not in out
    code, out = cli(str(tmp_path / "clean.py"))
    assert code == 0 and out.startswith("secret-scan: OK")
    assert cli(str(tmp_path / "data.sqlite3"))[0] == 1
    assert cli(str(tmp_path / "blob.bin"))[0] == 1, "unknown binaries outside docs/ fail"
    assert cli()[0] == 2, "no arguments is an error, not a pass"


def make_repo(tmp_path: Path):
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    git("config", ".".join(["core", "hooksPath"]), "/nonexistent")
    return git


def test_history_finds_removed_secrets_merges_and_messages(tmp_path):
    git = make_repo(tmp_path)
    (tmp_path / "a.txt").write_text(f'token = "{token44()}"\n', encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", "add")
    (tmp_path / "a.txt").write_text("clean\n", encoding="utf-8")
    git("commit", "-q", "-am", f"remove {UA_IBAN}")
    git("switch", "-q", "-c", "side")
    (tmp_path / "b.txt").write_text("side\n", encoding="utf-8")
    git("add", "b.txt")
    git("commit", "-q", "-m", "side")
    git("switch", "-q", "main")
    (tmp_path / "c.txt").write_text("main\n", encoding="utf-8")
    git("add", "c.txt")
    git("commit", "-q", "-m", "main")
    git("merge", "-q", "--no-commit", "side")
    (tmp_path / "merge.txt").write_text(f'secret = "{token44()}"\n', encoding="utf-8")
    git("add", "merge.txt")
    git("commit", "-q", "-m", "merge")
    git("rm", "-q", "merge.txt")
    git("commit", "-q", "-m", "cleanup")

    assert cli("--all", cwd=tmp_path)[0] == 0, "working tree is clean"
    code, out = cli("--history", "--all", cwd=tmp_path)
    assert code == 1
    assert "a.txt" in out and "merge.txt" in out and "message:1: iban" in out


def test_messages(tmp_path):
    msg = tmp_path / "MSG"
    msg.write_text("fix: clean\n", encoding="utf-8")
    assert cli("--message", str(msg))[0] == 0
    msg.write_text(f"fix\n# {UA_IBAN}\n", encoding="utf-8")
    assert cli("--message", str(msg))[0] == 1, "# lines are kept by `git commit -m`"
    scissors = "# ------------------------ >8 ------------------------"
    msg.write_text(f"fix\n{scissors}\n# Do not modify or remove the line above.\n+ {UA_IBAN}\n", encoding="utf-8")
    assert cli("--message", str(msg))[0] == 0, "git's own verbose diff is not part of the message"
    msg.write_text(f"fix\n{scissors}\n{UA_IBAN}\n", encoding="utf-8")
    assert cli("--message", str(msg))[0] == 1, "a typed scissors line is scanned"
    assert cli("--message", "-", stdin=f"tag {UA_IBAN}\n")[0] == 1


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_runs_through_a_symlinked_path(tmp_path):
    link = tmp_path / "linked"
    try:
        link.symlink_to(SCANNER.parent, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks here")
    (tmp_path / "leaky.py").write_text(f'x = "{token44()}"\n', encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(link / "secret_scan.py"), str(tmp_path / "leaky.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 1


def test_staged_names_are_literal_not_globs(tmp_path):
    git = make_repo(tmp_path)
    (tmp_path / "xA").write_text("clean\n", encoding="utf-8")
    try:
        (tmp_path / "x[A]").write_text(f'token = "{token44()}"\n', encoding="utf-8")
    except OSError:
        pytest.skip("file system does not allow [] in names")
    git("add", "xA", "x[A]")
    code, out = cli("--staged", cwd=tmp_path)
    assert code == 1, out
    assert "x[A]" in out


def test_telegram_tokens_in_urls_and_encoded():
    token = "1234567" + "89:" + secrets.token_urlsafe(30)[:35]
    host = ".".join(["api", "telegram", "org"])
    for line in (
        f"https://{host}/bot{token}",
        f'"bot{token}"',
        f"https://{host}/bot{token}/getMe",
        "bot" + token.replace(":", "%3A"),
        f"id {token} x",
    ):
        assert "telegram-bot-token" in rules_in(line), line


def test_repository_is_clean():
    code, out = cli("--all", cwd=ROOT)
    assert code == 0, out
