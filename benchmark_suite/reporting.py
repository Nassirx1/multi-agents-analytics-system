from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .runner import BenchmarkStatus, BenchmarkSuiteResult


JSON_RECEIPT_NAME = "benchmark_results.json"
MARKDOWN_RECEIPT_NAME = "benchmark_results.md"


def write_benchmark_receipts(
    suite: BenchmarkSuiteResult,
    output_dir: Path,
) -> dict[str, str]:
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / JSON_RECEIPT_NAME
    markdown_path = output_dir / MARKDOWN_RECEIPT_NAME
    json_path.write_text(json.dumps(suite.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_path.write_text(render_benchmark_markdown(suite), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def render_benchmark_markdown(suite: BenchmarkSuiteResult) -> str:
    status = "PASS" if suite.passed else "FAIL"
    lines = [
        "# Multi-Agent Analytics Benchmark Receipt",
        "",
        f"**Suite status:** {status}",
        "",
        f"- Suite ID: `{_cell(suite.suite_id)}`",
        f"- Deterministic result fingerprint: `{_cell(suite.result_fingerprint)}`",
        f"- Started: `{_cell(suite.started_at)}`",
        f"- Duration: `{suite.duration_ms:.3f} ms`",
        f"- Catalog cases accounted for: `{len(suite.accounted_benchmark_ids)}/{suite.catalog_case_count}`",
        f"- Executions: `{suite.execution_count}`",
        (
            "- Results: "
            f"`{suite.counts.get(BenchmarkStatus.PASS.value, 0)} pass`, "
            f"`{suite.counts.get(BenchmarkStatus.FAIL.value, 0)} fail`, "
            f"`{suite.counts.get(BenchmarkStatus.SKIP.value, 0)} explicit skip`"
        ),
        "",
    ]
    if suite.missing_benchmark_ids:
        lines.extend(
            [
                "## Catalog Accounting Failure",
                "",
                "Missing benchmark IDs: " + ", ".join(f"`{_cell(item)}`" for item in suite.missing_benchmark_ids),
                "",
            ]
        )

    lines.extend(["## Quality Dimensions", ""])
    if suite.dimension_summary:
        lines.extend(
            [
                "| Dimension | Evaluations | Mean / 100 | Minimum | Maximum |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for name, summary in suite.dimension_summary.items():
            lines.append(
                f"| {_cell(name)} | {summary['count']} | {_score(summary.get('mean'))} | "
                f"{_score(summary.get('minimum'))} | {_score(summary.get('maximum'))} |"
            )
    else:
        lines.append("No scored quality dimensions were produced.")
    lines.append("")

    lines.extend(["## Route Summary", ""])
    lines.extend(_summary_table(suite.route_summary, "Route"))
    lines.extend(["", "## Target Summary", ""])
    lines.extend(_summary_table(suite.target_summary, "Target"))
    lines.extend(
        [
            "",
            "## Benchmark Results",
            "",
            "| Status | Benchmark | Route | Target | Duration | Score | Note |",
            "|---|---|---|---|---:|---:|---|",
        ]
    )
    for result in suite.results:
        note = result.skipped_reason
        if not note and result.diagnostics:
            note = result.diagnostics[0]
        if result.failure_kind:
            note = f"{result.failure_kind}: {note}" if note else result.failure_kind
        lines.append(
            f"| {_status_icon(result.status)} {_cell(result.status.upper())} | "
            f"`{_cell(result.benchmark_id)}`<br>{_cell(result.name)} | "
            f"{_cell(result.route)} | {_cell(result.target)} | "
            f"{result.duration_ms:.3f} ms | {_score(result.overall_score)} | {_cell(note)} |"
        )

    diagnostic_results = [item for item in suite.results if item.diagnostics or item.skipped_reason]
    if diagnostic_results:
        lines.extend(["", "## Diagnostics", ""])
        for result in diagnostic_results:
            lines.append(f"### `{_cell(result.execution_id)}`")
            lines.append("")
            if result.skipped_reason:
                lines.append(f"- Skip reason: {_cell(result.skipped_reason)}")
            if result.failure_kind:
                lines.append(f"- Failure kind: `{_cell(result.failure_kind)}`")
            if result.error_type:
                lines.append(f"- Error type: `{_cell(result.error_type)}`")
            for diagnostic in result.diagnostics:
                lines.append(f"- {_cell(diagnostic)}")
            lines.append("")

    lines.extend(
        [
            "## Interpretation",
            "",
            "A skip is acceptable only when it is explicit (for example a live-only check without environment credentials). "
            "Timeouts, executor errors, missing scores, route leakage, and assertion failures are recorded as failures.",
            "",
        ]
    )
    return "\n".join(lines)


def _summary_table(summary: Mapping[str, Mapping[str, Any]], label: str) -> list[str]:
    lines = [
        f"| {label} | Pass | Fail | Skip |",
        "|---|---:|---:|---:|",
    ]
    if not summary:
        return lines + [f"| No {label.lower()} results | 0 | 0 | 0 |"]
    for name, counts in summary.items():
        lines.append(
            f"| {_cell(name)} | {int(counts.get('pass', 0))} | "
            f"{int(counts.get('fail', 0))} | {int(counts.get('skip', 0))} |"
        )
    return lines


def _status_icon(status: str) -> str:
    return {"pass": "[PASS]", "fail": "[FAIL]", "skip": "[SKIP]"}.get(status, "[INFO]")


def _score(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _cell(value: Any) -> str:
    text = str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())
