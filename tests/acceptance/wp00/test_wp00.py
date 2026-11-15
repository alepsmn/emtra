import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _agents_targets() -> set[str]:
    """Make targets named in the command table of AGENTS.md (`make xxx ...`)."""
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    section = text.split("## Comandos", 1)[1].split("\n## ", 1)[0]
    targets: set[str] = set()
    for cell in re.findall(r"`([^`]+)`", section):
        for part in cell.split(" / "):
            m = re.match(r"make ([a-z0-9-]+)", part.strip())
            if m:
                targets.add(m.group(1))
    return targets


def test_makefile_has_all_targets():
    expected = _agents_targets()
    assert len(expected) >= 14, expected
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    defined = set(re.findall(r"^([a-z0-9-]+):", makefile, flags=re.M))
    assert expected <= defined, expected - defined


def test_compose_is_valid():
    r = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "infra" / "docker-compose.yml"), "config"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
