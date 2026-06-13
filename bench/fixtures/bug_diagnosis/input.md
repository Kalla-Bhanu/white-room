# Fixture: Bug Diagnosis

The command `python -m cli.main bench fixtures list` prints `no fixtures` even after fixture folders are created.

Observed state:
- `bench/fixtures/README.md` exists.
- The loader only reports folders with both `input.md` and `rubric.md`.
- One folder is missing its rubric file.

Diagnose the likely cause and the next fix.
