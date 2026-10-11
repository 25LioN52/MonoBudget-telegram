"""Repository invariants: hardening in Docker/compose/workflows, no hidden characters."""

from __future__ import annotations

import re
import subprocess

import yaml

from .conftest import ROOT

TEXT_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".toml", ".txt", ".json", ".env", ".cfg", ".ini", ""}
SKIP_DIRS = {".git", ".venv", ".ruff_cache", ".pytest_cache", "__pycache__"}


def tracked_text_files():
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    for name in filter(None, out):
        path = ROOT / name
        if path.is_file() and path.suffix in TEXT_SUFFIXES and not SKIP_DIRS & set(path.parts):
            yield path


def is_hidden(c: int, code: bool) -> bool:
    if code and c in (0xA0, 0x2212):  # in code, even visible look-alikes (NBSP, minus) are built with chr()
        return True
    return (
        (c < 32 and c not in (9, 10, 13))
        or c == 0x7F
        or 0x80 <= c <= 0x9F
        or c in (0xAD, 0xFEFF)
        or 0x200B <= c <= 0x200F
        or 0x2028 <= c <= 0x202E
        or 0x2060 <= c <= 0x2069
        or 0xE0000 <= c <= 0xE007F
    )


def test_no_literal_hidden_or_control_characters():
    """Build such characters with chr(); literal ones make code read differently than it runs."""
    found = []
    for path in tracked_text_files():
        code = path.suffix == ".py"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            found += [f"{path.relative_to(ROOT)}:{number} U+{ord(ch):04X}" for ch in line if is_hidden(ord(ch), code)]
    assert found == []


def test_dockerfile_hardening():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    froms = re.findall(r"^FROM (\S+)", text, re.M)
    assert froms and all("@sha256:" in f for f in froms), "base images pinned by digest"
    assert re.search(r"COPY --from=\S+@sha256:", text), "uv image pinned by digest"
    assert "uv sync --frozen --no-dev" in text
    assert "pip uninstall" in text
    assert re.search(r"^USER 10001", text, re.M)
    assert "EXPOSE" not in text
    assert "HEALTHCHECK" in text


def test_dockerignore_keeps_secrets_out_of_the_build():
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert lines[lines.index("*")] == "*"  # deny everything, then allow-list
    assert all(not ln.startswith("!") or ln in ("!pyproject.toml", "!uv.lock", "!src/") for ln in lines)


def test_compose_hardening():
    service = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["monobudget"]
    assert "ports" not in service and "network_mode" not in service
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["user"] and service["mem_limit"] and service["pids_limit"]
    assert set(service["secrets"]) == {"telegram_bot_token", "mono_token"}
    assert "privileged" not in service
    assert service["logging"]["options"]["max-size"]
    assert service["restart"] == "unless-stopped"
    env = "\n".join(service["environment"].keys())
    assert "TOKEN=" not in env and "TELEGRAM_BOT_TOKEN_FILE" in env


def test_example_env_has_no_tokens():
    text = (ROOT / "example.env").read_text(encoding="utf-8")
    assert not re.search(r"^(TELEGRAM_BOT_TOKEN|MONO_TOKEN)=\S", text, re.M)


def _workflows():
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        yield path, yaml.safe_load(path.read_text(encoding="utf-8")), path.read_text(encoding="utf-8")


def test_workflows_are_pinned_and_least_privilege():
    for path, data, text in _workflows():
        assert data["permissions"] == {"contents": "read"}, path.name
        for ref in re.findall(r"uses:\s*(\S+)", text):
            if ref.startswith("./.github/workflows/"):
                continue  # this repository's own reusable workflow, same commit
            assert re.search(r"@[0-9a-f]{40}$", ref), f"{path.name}: {ref} not pinned by SHA"
        assert "pull_request_target" not in text
        assert "${{ github.event" not in text, "no untrusted event data in workflows"
        for job in data["jobs"].values():
            for step in job.get("steps", []):  # reusable-workflow jobs have no steps
                if step.get("uses", "").startswith("actions/checkout@"):
                    assert step["with"]["persist-credentials"] is False


def test_release_permissions_are_scoped_to_the_release_job():
    data = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    job = data["jobs"]["image"]
    assert job["permissions"]["packages"] == "write"
    assert job["permissions"]["id-token"] == "write"
    assert set(job["needs"]) == {"checks", "verify"}, "publish only after CI and tag verification"
    assert data["jobs"]["checks"]["uses"] == "./.github/workflows/ci.yml"
    assert "merge-base --is-ancestor" in data["jobs"]["verify"]["steps"][1]["run"]
    triggers = data[True] if True in data else data["on"]  # YAML 1.1 parses "on" as True
    assert set(triggers) == {"push"}, "no manual or branch releases"
    assert triggers["push"] == {"tags": ["v*.*.*"]}
    for name, other in data["jobs"].items():
        if name != "image":
            assert "packages" not in (other.get("permissions") or {}), name


def test_gitignore_covers_secrets_and_data():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in (".env", "secrets/", "data/", "*.sqlite3", "backups/", ".claude/settings.local.json"):
        assert entry in text, entry


def test_git_hooks_fail_closed():
    for hook in ("pre-commit", "commit-msg", "pre-push"):
        text = (ROOT / ".githooks" / hook).read_text(encoding="utf-8")
        assert "scripts/secret_scan.py" in text and "secret-scan: OK" in text
    modes = subprocess.run(
        ["git", "ls-files", "-s", ".githooks"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    for line in filter(None, modes.split("\n")):
        assert line.startswith("100755"), f"hook not executable in git: {line}"
