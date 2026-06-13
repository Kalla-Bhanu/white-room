from __future__ import annotations

from core.reindex import reindex_database


def run_reindex() -> str:
    result = reindex_database()
    return (
        "reindexed SQLite from files "
        f"(projects={result.project_count}, brain_files={result.brain_file_count}, "
        f"tasks={result.task_count}, handoffs={result.handoff_count}, decisions={result.decision_count}, "
        f"errors={result.error_count}, endpoints={result.endpoint_count}, usage_events={result.usage_event_count}, "
        f"routes={result.route_count}, benchmarks={result.benchmark_count}, "
        f"bench_fixtures={result.bench_fixture_count}, bench_runs={result.bench_run_count})"
    )
