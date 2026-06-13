# Release Process

WHITE ROOM releases should be treated as sanitized source snapshots. Runtime state is private by default.

## Release Gate

```powershell
python scripts/verify_release.py
```

The release gate checks:

- required Python modules
- localhost-safe checkout assumptions
- private-string scan
- secret/provider/security tests

## Manual Review

Before tagging:

- Review `git status --short`
- Confirm no `data/`, `projects/`, `.env`, or `secrets.local.json` files are staged
- Confirm screenshots are captured from sanitized demo state
- Confirm README and docs do not reference private providers, emails, paths, or account dashboards
- Confirm the tour GIF still renders in GitHub

## Tagging

Use semantic-ish alpha tags:

```powershell
git tag v0.1.0-alpha
git push origin v0.1.0-alpha
```

## Release Notes Template

```text
## WHITE ROOM vX.Y.Z

### Highlights
- Local-first project cockpit
- Provider routing and approval gates
- Security model and sanitized product tour

### Verification
- python scripts/verify_release.py

### Known Limits
- Local developer workbench, not hosted SaaS
- Provider-specific hardening continues
- Full integration suite is still being stabilized
```
