from __future__ import annotations

import argparse
import sys
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from benchmark_suite.runner import (  # noqa: E402
    ANALYTICS_REPORT,
    HTML_DASHBOARD,
    SHARED_ROUTE,
    run_default_catalog,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run deterministic workflow and output-quality benchmarks with complete JSON/Markdown accounting."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=WORKSPACE / "evals" / "benchmark_pilot",
        help="Receipt directory (default: evals/benchmark_pilot).",
    )
    parser.add_argument(
        "--route",
        action="append",
        choices=(ANALYTICS_REPORT, HTML_DASHBOARD, SHARED_ROUTE),
        help="Restrict execution to a route; repeat for multiple routes.",
    )
    parser.add_argument("--benchmark", action="append", help="Run one versioned benchmark ID; repeat as needed.")
    parser.add_argument("--timeout-seconds", type=float, help="Override every per-benchmark timeout.")
    parser.add_argument(
        "--include-live",
        action="store_true",
        help="Enable non-deterministic/live checks. Required credentials are still read only from the environment.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout_seconds is not None and args.timeout_seconds <= 0:
        build_parser().error("--timeout-seconds must be greater than zero")
    try:
        result = run_default_catalog(
            output_dir=args.output_dir,
            include_live=args.include_live,
            routes=args.route,
            benchmark_ids=args.benchmark,
            timeout_seconds=args.timeout_seconds,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"Benchmark suite configuration/execution error: {exc}", file=sys.stderr)
        return 2

    print(f"Benchmark suite: {'PASS' if result.passed else 'FAIL'}")
    print(
        f"Results: {result.counts.get('pass', 0)} pass, "
        f"{result.counts.get('fail', 0)} fail, {result.counts.get('skip', 0)} explicit skip"
    )
    print(f"JSON receipt: {(args.output_dir / 'benchmark_results.json').resolve()}")
    print(f"Markdown receipt: {(args.output_dir / 'benchmark_results.md').resolve()}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

