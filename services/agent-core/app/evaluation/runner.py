"""Evaluation runner: executes an EvaluationRun's experiment for real.

Reuses the repository benchmark (benchmarks/run_benchmark.py) unchanged —
that script drives the *production* scheduler / verification / self-loop
engines over pre-declared task sets and computes the group summaries.
This module only locates and loads it, runs it, and shapes the summary
for the configured mode into the metrics dict stored on the
EvaluationRun row. No second implementation of the experiment exists,
so numbers produced here cannot diverge from the published benchmark.

No LLM is involved (same as the benchmark itself): token usage is
recorded as 0, honestly. Runs are serialised with a lock because the
benchmark's self-loop part rebuilds shared on-disk workspaces.
"""
import importlib.util
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()
_BENCH = None

SUPPORTED_MODES = ("single", "fixed", "round_robin", "adaptive")


def _benchmark_path() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[4] / "benchmarks" / "run_benchmark.py",  # repo layout
        Path.home() / "workspace" / "agentflow-os" / "benchmarks" / "run_benchmark.py",
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError("benchmarks/run_benchmark.py not found")


def _load_benchmark():
    global _BENCH
    if _BENCH is None:
        spec = importlib.util.spec_from_file_location(
            "agentflow_benchmark", _benchmark_path())
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _BENCH = mod
    return _BENCH


def run_evaluation(mode: str) -> dict:
    """Run the full benchmark once and return the metrics for `mode`.

    success_rate / avg_latency come from the scheduler part for the
    configured mode (6 pre-declared tasks, production select_agent);
    recovery_rate comes from the Self-Loop part with repairs enabled
    (production SelfLoop + real pytest verification), the product's
    default recovery behaviour, independent of scheduling mode.
    """
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported evaluation mode: {mode}")
    bench = _load_benchmark()
    with _LOCK:
        runs = bench.run_scheduler_part() + bench.run_selfloop_part()
        groups = bench.summarize(runs)
    sched = groups[mode]
    loop = groups["self_loop_on"]
    return {
        "success_rate": sched["success_rate"],
        "avg_latency": sched["avg_seconds"],
        "recovery_rate": loop["repair_success_rate"],
        "scheduler": {"runs": sched["runs"], "successes": sched["successes"],
                      "failures": sched["failures"],
                      "total_seconds": sched["total_seconds"]},
        "self_loop": {"runs": loop["runs"], "successes": loop["successes"],
                      "initially_failed": loop["initially_failed"],
                      "repaired": loop["repaired"]},
        "token_usage": 0,
        "llm_called": False,
        "engine": "benchmarks/run_benchmark.py (production scheduler/verification/self-loop)",
        "run_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
