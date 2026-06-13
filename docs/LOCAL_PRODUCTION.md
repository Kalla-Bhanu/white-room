# Local Production Guide

WHITE ROOM can be operated as a production-grade local developer workbench. It is not currently a production SaaS platform.

## Supported Production Shape

Use WHITE ROOM as:

- a local app bound to `127.0.0.1`
- an internal fork for a single developer or trusted team
- a security-conscious AI workflow cockpit
- a provider-routing and project-memory skeleton for extension

Do not treat the current release as:

- a multi-tenant hosted SaaS
- an internet-exposed service
- a replacement for identity, device, or cloud security controls
- a guarantee that third-party AI providers are private

## Operator Baseline

1. Run from a trusted checkout.
2. Use Python 3.11+ in a virtual environment.
3. Keep runtime state out of git.
4. Bind to `127.0.0.1` by default.
5. Use local lanes for low-risk work.
6. Use cloud lanes only for scoped prompts that need them.
7. Review approval gates and route decisions.
8. Run `python scripts/verify_release.py` before publishing or sharing.

## Localhost Binding

The built-in server command binds to localhost:

```powershell
python -m cli.main serve --port 8765
```

If you expose the app beyond localhost, add your own:

- authentication
- TLS
- reverse proxy access controls
- network restrictions
- log redaction review
- backup/restore plan for local runtime state

## Data Handling

Local runtime state lives in:

```text
data/       SQLite runtime database
projects/   project brain files, packets, handoffs, artifacts
```

Secrets live in:

```text
process environment
.env
secrets.local.json
```

The security model assumes these stay on the local machine unless you intentionally export or back them up.

## Production Readiness Checklist

- [ ] `python scripts/doctor.py`
- [ ] `python scripts/bootstrap_demo.py`
- [ ] `python scripts/verify_release.py`
- [ ] Confirm `.env`, `secrets.local.json`, `data/`, and `projects/` are ignored
- [ ] Confirm provider keys are absent from screenshots and docs
- [ ] Confirm cloud lanes are approval-gated
- [ ] Confirm provider base URLs are API URLs, not dashboards
- [ ] Confirm public screenshots use sanitized demo state

## Incident Response Notes

If a key or private project memory is exposed:

1. Revoke the affected provider key.
2. Remove the exposed file or image.
3. Rotate any dependent credentials.
4. Re-run `python scripts/secret_scan.py`.
5. Review generated packets, logs, screenshots, and database exports.
6. Publish a clear note if a public release was affected.
