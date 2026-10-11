#!/usr/bin/env python3
"""Zero-dependency secret & personal-data scanner for this repository.

    python3 scripts/secret_scan.py --staged            files staged for commit (index content)
    python3 scripts/secret_scan.py --all               all tracked + untracked (non-ignored) files
    python3 scripts/secret_scan.py --history REVS...   every file version and commit message in
                                                       `git rev-list REVS` (e.g. --all, HEAD --not --remotes)
    python3 scripts/secret_scan.py --message FILE|-    a commit/tag message (file or stdin)
    python3 scripts/secret_scan.py FILE...             specific files

Prints "secret-scan: OK ..." and exits 0 only when nothing was found; callers
must check for that line as well as the exit code (fail closed). Exit 1 =
findings, 2 = usage/internal error. Findings are masked: a full secret is never
printed.

Detects: private keys; Telegram bot tokens; GitHub/AWS/AI/Slack tokens;
token-like assignments; high-entropy strings (e.g. a Monobank token); IBANs
with a valid checksum; card numbers (Luhn); hidden Unicode (bidi controls,
zero-width, tag characters); file types that hold secrets or personal data.
Test data must use invalid IBANs (check digits "00") and masked card numbers.
A false positive can be allowed per rule on its line: `secret-scan:allow <rule-id>`.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ALLOW_MARK = "secret-scan:allow"

FORBIDDEN_FILES = [
    (re.compile(r"(^|/)\.env(\..*)?$", re.I), "environment file with secrets"),
    (re.compile(r"(^|/)secrets/", re.I), "secrets directory"),
    (re.compile(r"(^|/)data/", re.I), "bot data directory (database)"),
    (re.compile(r"(^|/)backups?/", re.I), "database backups"),
    (re.compile(r"\.(sqlite3?|db)(-wal|-shm|-journal)?$", re.I), "SQLite database (real transactions)"),
    (re.compile(r"\.(pem|key|p12|pfx|keystore|jks|cer)$", re.I), "key or certificate"),
    (re.compile(r"(^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$", re.I), "SSH key"),
    (re.compile(r"(^|/)(mono|monobank|telegram|bot)[-_]?token", re.I), "token file"),
    (re.compile(r"\.(pdf|xls|xlsx|csv|ofx|qif|har)$", re.I), "statement export / HTTP archive"),
    (re.compile(r"(^|/)CLAUDE\.local\.md$", re.I), "personal Claude notes"),
    (
        re.compile(r"^(?!docs/).*\.(png|jpe?g|heic|heif|gif|webp|mov|mp4)$", re.I),
        "image/video outside docs/ (screenshots may show real data)",
    ),
]


def _char_class(ranges: list[tuple[int, int]]) -> str:
    return "[" + "".join(chr(a) if a == b else f"{chr(a)}-{chr(b)}" for a, b in ranges) + "]"


# Built from code points / pieces so this file does not match its own rules.
RULES: list[tuple[str, re.Pattern[str], object]] = [
    ("private-key", re.compile("-----BEGIN [A-Z ]*" + "PRIVATE KEY-----"), None),
    # Also inside Bot API URLs (".../bot<id>:<secret>") and URL-encoded (%3A).
    ("telegram-bot-token", re.compile(r"(?<![0-9])\d{5,15}(?::|%3[Aa])[A-Za-z0-9_-]{30,64}(?![A-Za-z0-9_-])"), None),
    ("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"), None),
    ("aws-access-key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), None),
    ("ai-api-key", re.compile(r"\bsk-(ant-)?[A-Za-z0-9_-]{32,}\b"), None),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), None),
    (
        "token-assignment",
        re.compile(
            r"(x-token|token|secret|api[_-]?key|password|passwd)[\"']?\s*[:=]\s*[\"'`]([A-Za-z0-9_\-+/=]{20,})[\"'`]",
            re.I,
        ),
        "entropy",
    ),
    (
        "hidden-unicode",
        re.compile(
            _char_class(
                [
                    (0x00AD, 0x00AD),
                    (0x061C, 0x061C),
                    (0x180E, 0x180E),
                    (0x200B, 0x200F),
                    (0x2028, 0x202E),
                    (0x2060, 0x2069),
                    (0xFEFF, 0xFEFF),
                    (0xE0000, 0xE007F),
                ]
            )
        ),
        None,
    ),
]

# IBAN lengths per country (ISO 13616); only exact lengths are checked, which keeps
# random text from passing the mod-97 checksum by chance.
IBAN_LENGTHS = {
    "AD": 24,
    "AE": 23,
    "AL": 28,
    "AT": 20,
    "AZ": 28,
    "BA": 20,
    "BE": 16,
    "BG": 22,
    "BH": 22,
    "BR": 29,
    "BY": 28,
    "CH": 21,
    "CR": 22,
    "CY": 28,
    "CZ": 24,
    "DE": 22,
    "DK": 18,
    "DO": 28,
    "EE": 20,
    "EG": 29,
    "ES": 24,
    "FI": 18,
    "FO": 18,
    "FR": 27,
    "GB": 22,
    "GE": 22,
    "GI": 23,
    "GL": 18,
    "GR": 27,
    "GT": 28,
    "HR": 21,
    "HU": 28,
    "IE": 22,
    "IL": 23,
    "IQ": 23,
    "IS": 26,
    "IT": 27,
    "JO": 30,
    "KW": 30,
    "KZ": 20,
    "LB": 28,
    "LC": 32,
    "LI": 21,
    "LT": 20,
    "LU": 20,
    "LV": 21,
    "MC": 27,
    "MD": 24,
    "ME": 22,
    "MK": 19,
    "MR": 27,
    "MT": 31,
    "MU": 30,
    "NL": 18,
    "NO": 15,
    "PK": 24,
    "PL": 28,
    "PS": 29,
    "PT": 25,
    "QA": 29,
    "RO": 24,
    "RS": 22,
    "SA": 24,
    "SC": 31,
    "SE": 24,
    "SI": 19,
    "SK": 24,
    "SM": 27,
    "ST": 25,
    "SV": 28,
    "TL": 23,
    "TN": 24,
    "TR": 26,
    "UA": 29,
    "VA": 22,
    "VG": 24,
    "XK": 20,
}
_SEPARATORS = " \t-" + chr(0xA0)
_HIGH_ENTROPY = re.compile(r"[A-Za-z0-9_\-+/]{40,}={0,2}")
_IBAN_START = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2})\d{2}")
_CARD = re.compile(r"\b[2-6]\d{3}(?:[ " + chr(0xA0) + r"-]?\d){9,15}\b")


def entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def is_valid_iban(raw: str) -> bool:
    iban = "".join(ch for ch in raw if ch not in _SEPARATORS).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 == 1


def is_luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def mask(value: str) -> str:
    return "*" * len(value) if len(value) <= 6 else f"{value[:4]}…({len(value)} chars)"


def _ibans(line: str) -> list[str]:
    found = []
    for m in _IBAN_START.finditer(line):
        length = IBAN_LENGTHS.get(m.group(1).upper())
        if not length:
            continue
        compact, raw = "", ""
        for ch in line[m.start() :]:
            if len(compact) == length:
                break
            if ch.isascii() and ch.isalnum():
                compact += ch
            elif ch not in _SEPARATORS:
                break
            raw += ch
        if len(compact) == length and is_valid_iban(compact):
            found.append(raw.strip())
    return found


def _allowed(line: str) -> set[str]:
    at = line.find(ALLOW_MARK)
    if at == -1:
        return set()
    return {w for w in re.split(r"[\s,]+", line[at + len(ALLOW_MARK) :].strip()) if re.fullmatch(r"[a-z-]+", w)}


def scan_text(text: str) -> list[tuple[int, str, str]]:
    """Returns (line number, rule id, masked sample) for every finding."""
    findings: list[tuple[int, str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        allowed = _allowed(line)

        def add(rule: str, value: str, n: int = number, ok: set[str] = allowed) -> None:
            if rule not in ok:
                findings.append((n, rule, mask(value)))

        for rule, pattern, check in RULES:
            for m in pattern.finditer(line):
                value = m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(0)
                if check == "entropy" and entropy(value) < 3.5:
                    continue
                add(rule, value)
        for m in _HIGH_ENTROPY.finditer(line):
            s = m.group(0)
            if re.search(r"[a-z]", s) and re.search(r"[A-Z]", s) and re.search(r"\d", s) and entropy(s) >= 4.5:
                add("high-entropy-string", s)
        for iban in _ibans(line):
            add("iban", iban)
        for m in _CARD.finditer(line):
            digits = re.sub(r"\D", "", m.group(0))
            if 13 <= len(digits) <= 19 and is_luhn(digits):
                add("card-number", m.group(0))
    return findings


def decode(data: bytes) -> str | None:
    """UTF-8, or UTF-16 with a BOM. None = binary."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def forbidden_reason(path: str) -> str | None:
    return next((why for pattern, why in FORBIDDEN_FILES if pattern.search(path)), None)


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def _scan_blob(label: str, path: str, data: bytes | None, problems: list[str]) -> None:
    reason = forbidden_reason(path)
    if reason:
        problems.append(f"{label}: forbidden file ({reason}) must not be committed")
    if data is None:
        return
    text = decode(data)
    if text is None:
        if not reason and not path.startswith("docs/"):
            problems.append(f"{label}: binary file outside docs/ cannot be scanned")
        return
    problems.extend(f"{label}:{n}: {rule} {sample}" for n, rule, sample in scan_text(text))


def _message_text(raw: str) -> str:
    """Cut only git's own verbose-diff block; a typed scissors line is kept by git, so it is scanned."""
    m = re.search(r"^# -+ >8 -+\n# Do not modify or remove the line above\.", raw, re.M)
    return raw[: m.start()] if m else raw


def _staged_blob(path: str) -> bytes:
    """Index content by object id. Literal pathspecs: "x[A]" must not match a sibling "xA"."""
    entry = git("--literal-pathspecs", "ls-files", "-s", "-z", "--", path).decode("utf-8", errors="replace")
    meta, _, name = entry.split("\0")[0].partition("\t")
    if name != path:
        raise IndexError("staged entry not found")
    return git("cat-file", "blob", meta.split()[1])


def scan_files(paths: list[str], staged: bool, problems: list[str]) -> int:
    for path in paths:
        norm = path.replace("\\", "/")
        if not staged and not Path(path).exists():
            continue  # tracked but deleted in the working tree
        try:
            data = _staged_blob(path) if staged else Path(path).read_bytes()
        except (OSError, IndexError, subprocess.CalledProcessError) as error:
            problems.append(f"{norm}: cannot be read ({type(error).__name__}) — failing closed")
            continue
        _scan_blob(norm, norm, data, problems)
    return len(paths)


def scan_history(revs: list[str], problems: list[str]) -> int:
    commits = git("rev-list", *revs).decode().split()
    for commit in commits:
        short = commit[:10]
        message = git("log", "-1", "--format=%B", commit).decode("utf-8", errors="replace")
        problems.extend(f"commit {short} message:{n}: {rule} {s}" for n, rule, s in scan_text(message))
        listing = git(
            "diff-tree", "--root", "-m", "--no-commit-id", "-r", "--name-only", "--diff-filter=ACMRT", "-z", commit
        )
        for path in dict.fromkeys(p for p in listing.decode("utf-8", errors="replace").split("\0") if p):
            try:
                data = git("show", f"{commit}:{path}")
            except subprocess.CalledProcessError:
                data = None
            _scan_blob(f"{short}:{path}", path, data, problems)
    return len(commits)


def main(argv: list[str]) -> int:
    problems: list[str] = []
    mode, rest = (argv[0], argv[1:]) if argv else ("", [])
    if mode == "--staged":
        files = [
            p for p in git("diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z").decode().split("\0") if p
        ]
        summary = f"{scan_files(files, True, problems)} staged file(s)"
    elif mode == "--all":
        files = [
            p for p in git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0") if p
        ]
        summary = f"{scan_files(files, False, problems)} file(s)"
    elif mode == "--history":
        if not rest:
            raise ValueError("--history needs revisions, e.g. --history --all")
        summary = f"{scan_history(rest, problems)} commit(s)"
    elif mode == "--message":
        if not rest:
            raise ValueError("--message needs a file (or - for stdin)")
        raw = sys.stdin.read() if rest[0] == "-" else Path(rest[0]).read_text(encoding="utf-8", errors="replace")
        problems.extend(f"message:{n}: {rule} {s}" for n, rule, s in scan_text(_message_text(raw)))
        summary = "message"
    elif mode and not mode.startswith("--"):
        summary = f"{scan_files(argv, False, problems)} file(s)"
    else:
        print(__doc__, file=sys.stderr)
        return 2

    if problems:
        print(f"secret-scan: {len(problems)} problem(s) found:\n  " + "\n  ".join(problems), file=sys.stderr)
        print(
            f'\nRemove the data or use synthetic test values. Only for a false positive, add "{ALLOW_MARK} <rule-id>" '
            "to the line.",
            file=sys.stderr,
        )
        return 1
    print(f"secret-scan: OK ({summary} checked)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as error:  # fail closed with a clear status
        print(f"secret-scan: ERROR {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(2)
