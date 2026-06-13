# Install And Run

WHITE ROOM is packaged as a local-first developer workbench. The intended production shape is a trusted local checkout or internal fork, not a hosted multi-tenant SaaS.

## Requirements

- Python 3.11 or newer
- Git
- Optional: Ollama or LM Studio for local model lanes
- Optional: provider API keys for cloud lanes

## Fresh Clone

```powershell
git clone https://github.com/Kalla-Bhanu/white-room.git
cd white-room
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python scripts/doctor.py
python scripts/bootstrap_demo.py
python -m cli.main serve --port 8765
```

Open:

```text
http://127.0.0.1:8765/chat/white-room
```

## One-Command Local Demo

PowerShell:

```powershell
.\scripts\run_local.ps1
```

PowerShell with a clean demo reset:

```powershell
.\scripts\run_local.ps1 -ResetDemo
```

macOS/Linux:

```bash
bash scripts/run_local.sh
```

## Verify A Fork

Run the release gate:

```powershell
python scripts/verify_release.py
```

This runs:

- environment/package doctor checks
- private-string release scan
- secret/provider/security tests

## Runtime Files

These files are local runtime state and should not be committed:

- `.env`
- `secrets.local.json`
- `secrets.enc`
- `data/`
- `projects/`

## Provider Keys

For local-only use, no cloud keys are required. For cloud/provider lanes, use Settings or environment variables. The UI should show only key presence and fingerprints, never raw secrets.
