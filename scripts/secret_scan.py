from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    ".deps",
    ".pytest_cache",
    "__pycache__",
    "data",
    "dist",
    "projects",
}
EXCLUDED_EXTENSIONS = {".png", ".gif", ".jpg", ".jpeg", ".webp", ".ico", ".pyc", ".db", ".sqlite", ".sqlite3"}
EXCLUDED_FILES = {Path("scripts/secret_scan.py")}

PATTERNS = [
    re.compile(r"C:\\Users\\bhanu", re.IGNORECASE),
    re.compile(r"gmail\.com", re.IGNORECASE),
    re.compile(r"USCIS|Wesleyan|Northeastern", re.IGNORECASE),
    re.compile(r"kbloadbalancer|198\.199\.88\.10", re.IGNORECASE),
    re.compile(r"gsk_[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"fp_[a-f0-9]{8}"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan tracked-source candidates for private strings.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()

    findings: list[str] = []
    for path in _iter_files(args.root.resolve()):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{path.relative_to(args.root)}:{line}: {match.group(0)}")

    if findings:
        print("Potential private strings found:")
        for finding in findings:
            print(finding)
        raise SystemExit(1)
    print("No private strings matched the release scan.")


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        relative = path.relative_to(root)
        if relative in EXCLUDED_FILES:
            continue
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() in EXCLUDED_EXTENSIONS:
            continue
        yield path


if __name__ == "__main__":
    main()
