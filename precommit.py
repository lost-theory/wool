"""
Run linters and tests.

Usage:
    uv run precommit.py
"""

import subprocess
import sys
from pathlib import Path

BIN = Path(sys.executable).parent


def run(label: str, cmd: list[Path | str]) -> int:
    print(f"\n== {label} ==")
    return subprocess.run(cmd, check=False).returncode


def main() -> None:
    results = [
        ("isort", run("isort", [BIN / "ruff", "check", "--select", "I", "--fix", "."])),
        ("black", run("black", [BIN / "black", "--check", "."])),
        ("flake8", run("flake8", [BIN / "flake8"])),
        ("pylint", run("pylint", [BIN / "pylint", "wool/"])),
        ("mypy", run("mypy", [BIN / "mypy", "--strict", "."])),
        ("pyrefly", run("pyrefly", [BIN / "pyrefly", "check", "--summary=full"])),
        ("pytest", run("pytest", [BIN / "pytest", "tests/", "--cov=wool", "--cov-report=html", "--cov-fail-under=90"])),
    ]

    print("\n== Results ==")
    for name, code in results:
        print(f"* {name}: {'OK' if code == 0 else 'FAIL'}")

    if any(code != 0 for _, code in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
