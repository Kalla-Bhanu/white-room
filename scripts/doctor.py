from __future__ import annotations

import argparse
import importlib.util
import socket
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


REQUIRED_MODULES = ["typer", "jinja2", "dotenv", "fastapi", "uvicorn", "httpx"]
DEV_MODULES = ["pytest"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a local WHITE ROOM checkout.")
    parser.add_argument("--ci", action="store_true", help="Fail on missing dev-only tooling.")
    args = parser.parse_args()

    checks: list[tuple[str, bool, str]] = []
    checks.append(("Python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0]))
    checks.append(("Repository root", (ROOT / "pyproject.toml").exists(), str(ROOT)))
    checks.append(("Templates present", (ROOT / "templates").is_dir(), "templates/"))
    checks.append(("Web app present", (ROOT / "web" / "server.py").exists(), "web/server.py"))
    checks.append(("Runtime data ignored", _gitignore_contains("data/"), ".gitignore"))
    checks.append(("Runtime projects ignored", _gitignore_contains("projects/"), ".gitignore"))

    for module in REQUIRED_MODULES:
        checks.append((f"Import {module}", importlib.util.find_spec(module) is not None, module))
    if args.ci:
        for module in DEV_MODULES:
            checks.append((f"Import {module}", importlib.util.find_spec(module) is not None, module))

    checks.append(("Localhost server port usable", _port_usable("127.0.0.1", 8765), "127.0.0.1:8765"))

    failed = False
    for name, ok, detail in checks:
        status = "ok" if ok else "fail"
        print(f"[{status}] {name} - {detail}")
        failed = failed or not ok

    if failed:
        raise SystemExit(1)


def _gitignore_contains(value: str) -> bool:
    path = ROOT / ".gitignore"
    if not path.exists():
        return False
    return value in path.read_text(encoding="utf-8").splitlines()


def _port_usable(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


if __name__ == "__main__":
    main()
