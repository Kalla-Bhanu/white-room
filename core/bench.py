from __future__ import annotations

from datetime import datetime, timezone
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapters.lmstudio_local import LMStudioLocalAdapter
from adapters.ollama_local import OllamaLocalAdapter
from core.db import APP_ROOT, connect, init_db


FIXTURES_ROOT = APP_ROOT / "bench" / "fixtures"


@dataclass(frozen=True)
class BenchFixture:
    task_type: str
    folder: Path
    input_path: Path
    rubric_path: Path

    def read_input(self) -> str:
        return self.input_path.read_text(encoding="utf-8")

    def read_rubric(self) -> str:
        return self.rubric_path.read_text(encoding="utf-8")


@dataclass(frozen=True)
class BenchRunResult:
    fixture: BenchFixture
    output_path: Path
    score: float
    verified: bool
    run_id: int


@dataclass(frozen=True)
class BenchRunRecord:
    run_id: int
    task_type: str
    output_path: str
    score: float
    verified: bool
    run_at: str


def list_fixtures(fixtures_root: Path = FIXTURES_ROOT) -> list[BenchFixture]:
    if not fixtures_root.exists():
        return []

    fixtures: list[BenchFixture] = []
    for folder in sorted(path for path in fixtures_root.iterdir() if path.is_dir()):
        input_path = folder / "input.md"
        rubric_path = folder / "rubric.md"
        if input_path.exists() and rubric_path.exists():
            fixtures.append(
                BenchFixture(
                    task_type=folder.name,
                    folder=folder,
                    input_path=input_path,
                    rubric_path=rubric_path,
                )
            )
    return fixtures


def load_fixture(task_type: str, fixtures_root: Path = FIXTURES_ROOT) -> BenchFixture:
    folder = fixtures_root / task_type
    if not folder.exists():
        raise ValueError(f"fixture '{task_type}' does not exist")
    if not folder.is_dir():
        raise ValueError(f"fixture '{task_type}' is not a folder")

    input_path = folder / "input.md"
    rubric_path = folder / "rubric.md"
    missing = [path.name for path in (input_path, rubric_path) if not path.exists()]
    if missing:
        raise ValueError(f"fixture '{task_type}' is missing {', '.join(missing)}")

    return BenchFixture(
        task_type=task_type,
        folder=folder,
        input_path=input_path,
        rubric_path=rubric_path,
    )


def score_fixture_output(
    task_type: str,
    output_path: Path,
    confirm_verified: bool = False,
    fixtures_root: Path = FIXTURES_ROOT,
) -> BenchRunResult:
    fixture = load_fixture(task_type, fixtures_root=fixtures_root)
    if not output_path.exists():
        raise ValueError(f"output file '{output_path}' does not exist")
    if not output_path.is_file():
        raise ValueError(f"output file '{output_path}' is not a file")

    output_text = output_path.read_text(encoding="utf-8")
    rubric_lines = _rubric_items(fixture.read_rubric())
    item_results = [_rubric_item_passed(line, output_text) for line in rubric_lines]
    score = round((sum(item_results) / len(item_results)) * 100.0, 1) if item_results else 0.0
    verified = confirm_verified and bool(item_results) and all(item_results)
    created_at = _utc_now()

    with connect() as conn:
        init_db(conn)
        fixture_id = _upsert_fixture(conn, fixture, created_at)
        cursor = conn.execute(
            """
            INSERT INTO bench_runs
                (endpoint_id, fixture_id, output_path, score, latency_ms, cost_est, verified, run_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                None,
                fixture_id,
                _relative_or_str(output_path),
                score,
                0,
                0.0,
                1 if verified else 0,
                created_at,
            ),
        )
        run_id = int(cursor.lastrowid)
        conn.commit()

    return BenchRunResult(
        fixture=fixture,
        output_path=output_path,
        score=score,
        verified=verified,
        run_id=run_id,
    )


def run_local_benchmark(endpoint_name: str, task_type: str, confirm: bool = False) -> BenchRunResult:
    fixture = load_fixture(task_type)
    adapter = _local_adapter(endpoint_name)
    payload = {
        "project_slug": "white-room",
        "task_type": task_type,
        "input_text": fixture.read_input(),
        "rubric_text": fixture.read_rubric(),
        "model": _default_model_name(endpoint_name),
    }

    try:
        result = adapter.call(adapter.prepare(payload))
    except Exception as exc:
        error_name = type(exc).__name__.lower()
        if any(token in error_name for token in ("timeout", "connect", "connection", "network")):
            raise RuntimeError(f"local server unavailable at {adapter.base_url}: {exc}") from exc
        raise RuntimeError(str(exc)) from exc

    output_text = str(result.get("text") or "").strip()
    if not output_text:
        raise RuntimeError("local model response did not include output text")

    output_path = _write_draft_output(endpoint_name, task_type, output_text)
    return score_fixture_output(task_type, output_path, confirm_verified=confirm)


def promote_verified_benchmark(endpoint_name: str, task_type: str, result: BenchRunResult) -> int:
    if not result.verified:
        raise RuntimeError("benchmark did not pass; not promoting to verified")

    created_at = _utc_now()
    with connect() as conn:
        init_db(conn)
        endpoint_id = _ensure_local_endpoint(conn, endpoint_name, result.output_path)
        cursor = conn.execute(
            """
            INSERT INTO benchmarks
                (endpoint_id, task_type, score, latency_ms, cost_est, verified, run_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (endpoint_id, task_type, result.score, 0, 0.0, 1, created_at),
        )
        benchmark_id = int(cursor.lastrowid)
        conn.commit()

    return benchmark_id


def list_bench_runs() -> list[BenchRunRecord]:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT
                r.id AS run_id,
                COALESCE(f.task_type, '') AS task_type,
                r.output_path AS output_path,
                r.score AS score,
                r.verified AS verified,
                r.run_at AS run_at
            FROM bench_runs AS r
            LEFT JOIN bench_fixtures AS f ON f.id = r.fixture_id
            ORDER BY r.run_at ASC, r.id ASC
            """
        ).fetchall()

    return [
        BenchRunRecord(
            run_id=int(row["run_id"]),
            task_type=str(row["task_type"]),
            output_path=str(row["output_path"]),
            score=float(row["score"]),
            verified=bool(row["verified"]),
            run_at=str(row["run_at"]),
        )
        for row in rows
    ]


def _upsert_fixture(conn: sqlite3.Connection, fixture: BenchFixture, created_at: str) -> int:
    row = conn.execute(
        """
        SELECT id
        FROM bench_fixtures
        WHERE task_type = ? AND input_path = ? AND rubric_path = ?
        """,
        (
            fixture.task_type,
            _relative_or_str(fixture.input_path),
            _relative_or_str(fixture.rubric_path),
        ),
    ).fetchone()
    if row is not None:
        return int(row["id"])

    cursor = conn.execute(
        """
        INSERT INTO bench_fixtures (task_type, input_path, rubric_path, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            fixture.task_type,
            _relative_or_str(fixture.input_path),
            _relative_or_str(fixture.rubric_path),
            created_at,
        ),
    )
    return int(cursor.lastrowid)


def _rubric_items(rubric_text: str) -> list[str]:
    return [line.strip() for line in rubric_text.splitlines() if line.strip().startswith("- [")]


def _rubric_item_passed(rubric_line: str, output_text: str) -> bool:
    keywords = _keywords_from_rubric_line(rubric_line)
    if not keywords:
        return False
    output = output_text.lower()
    return all(keyword in output for keyword in keywords)


def _keywords_from_rubric_line(rubric_line: str) -> list[str]:
    text = re.sub(r"^- \[[ xX]\]\s*", "", rubric_line).lower()
    words = re.findall(r"[a-z0-9]+", text)
    stopwords = {
        "the",
        "and",
        "or",
        "a",
        "an",
        "to",
        "of",
        "for",
        "in",
        "on",
        "with",
        "is",
        "are",
        "be",
        "that",
        "this",
        "it",
        "as",
        "by",
        "all",
        "no",
        "not",
        "only",
        "stays",
        "staying",
        "mentions",
        "captured",
        "capturing",
        "captured",
        "simple",
        "clear",
        "readable",
        "concise",
        "factual",
        "provided",
        "grounded",
        "evidence",
        "question",
        "questions",
        "output",
        "file",
        "files",
    }
    keywords = [word for word in words if len(word) > 3 and word not in stopwords]
    # Require at most three meaningful terms to keep the heuristic stable on short rubrics.
    return keywords[:3]


def _relative_or_str(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(APP_ROOT))
    except ValueError:
        return str(path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _local_adapter(endpoint_name: str):
    if endpoint_name in {"ollama-local", "ollama_local"}:
        return OllamaLocalAdapter()
    if endpoint_name in {"lmstudio-local", "lmstudio_local"}:
        return LMStudioLocalAdapter()
    raise ValueError(f"unsupported local endpoint '{endpoint_name}'")


def _default_model_name(endpoint_name: str) -> str:
    if endpoint_name in {"ollama-local", "ollama_local"}:
        return "llama3.1"
    if endpoint_name in {"lmstudio-local", "lmstudio_local"}:
        return "local-model"
    return "local-model"


def _write_draft_output(endpoint_name: str, task_type: str, output_text: str) -> Path:
    output_root = APP_ROOT / "data" / "bench_outputs" / endpoint_name.replace("_", "-")
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / f"{task_type}-{_utc_now().replace(':', '').replace('+00:00', 'Z')}.md"
    output_path.write_text(
        "Status: draft\n\n" + output_text.rstrip() + "\n",
        encoding="utf-8",
    )
    return output_path


def _ensure_local_endpoint(conn: sqlite3.Connection, endpoint_name: str, output_path: Path) -> int:
    row = conn.execute("SELECT id FROM endpoints WHERE name = ?", (endpoint_name,)).fetchone()
    if row is not None:
        return int(row["id"])

    endpoint_class = _endpoint_class_name(endpoint_name)
    base_url = _endpoint_base_url(endpoint_name)
    conn.execute(
        """
        INSERT INTO endpoints
            (name, endpoint_class, base_url, capabilities, tier, daily_limit, window_limit, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            endpoint_name,
            endpoint_class,
            base_url,
            f"verified benchmark for {endpoint_name}",
            "local",
            "manual",
            "manual",
            "available",
        ),
    )
    row = conn.execute("SELECT id FROM endpoints WHERE name = ?", (endpoint_name,)).fetchone()
    if row is None:
        raise RuntimeError(f"failed to create endpoint record for '{endpoint_name}'")
    return int(row["id"])


def _endpoint_class_name(endpoint_name: str) -> str:
    if endpoint_name in {"ollama-local", "ollama_local"}:
        return "ollama_local"
    if endpoint_name in {"lmstudio-local", "lmstudio_local"}:
        return "lmstudio_local"
    raise ValueError(f"unsupported local endpoint '{endpoint_name}'")


def _endpoint_base_url(endpoint_name: str) -> str:
    if endpoint_name in {"ollama-local", "ollama_local"}:
        return "http://127.0.0.1:11434"
    if endpoint_name in {"lmstudio-local", "lmstudio_local"}:
        return "http://127.0.0.1:1234"
    raise ValueError(f"unsupported local endpoint '{endpoint_name}'")
