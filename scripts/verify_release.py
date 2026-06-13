from __future__ import annotations

import subprocess
import sys


COMMANDS = [
    [sys.executable, "scripts/doctor.py", "--ci"],
    [sys.executable, "scripts/secret_scan.py"],
    [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_phase15_secrets.py",
        "tests/test_settings_providers.py",
        "tests/test_phase15_groq_models_and_gates.py",
        "-q",
    ],
]


def main() -> None:
    for command in COMMANDS:
        print(f":: {' '.join(command)}")
        subprocess.run(command, check=True)
    print("Release verification passed.")


if __name__ == "__main__":
    main()
