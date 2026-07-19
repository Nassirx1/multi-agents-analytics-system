from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import xlsxwriter


DEFAULT_RECEIPT = Path("evals/benchmark_pilot/benchmark_results.json")
DEFAULT_OPTIMIZER_REVIEW = Path("evals/recursive_benchmark_loop/optimizer_b_final.json")


def export_benchmark_workbook(
    receipt_path: Path,
    output_path: Path,
    *,
    optimizer_review_path: Path | None = DEFAULT_OPTIMIZER_REVIEW,
) -> Path:
    receipt_path = Path(receipt_path).resolve()
    output_path = Path(output_path).resolve()
    receipt = _read_mapping(receipt_path)
    optimizer_review = (
        _read_mapping(Path(optimizer_review_path).resolve())
        if optimizer_review_path is not None and Path(optimizer_review_path).is_file()
        else {}
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    workbook = xlsxwriter.Workbook(output_path)
    workbook.set_properties(
        {
            "title": "Multi-Agent Analytics Benchmark Results",
            "subject": "Workflow, output-quality, and improvement-planning benchmark receipt",
            "author": "Multi-Agent Analytics System",
            "comments": f"Source receipt: {receipt_path}",
        }
    )
    formats = _formats(workbook)
    _write_summary(workbook, formats, receipt, receipt_path)
    _write_results(workbook, formats, receipt)
    _write_improvement_plan(workbook, formats, receipt, optimizer_review)
    _write_catalog(workbook, formats, receipt)
    workbook.close()
    return output_path


def _formats(workbook: xlsxwriter.Workbook) -> dict[str, Any]:
    return {
        "title": workbook.add_format({"bold": True, "font_size": 18, "font_color": "#16324F"}),
        "section": workbook.add_format(
            {"bold": True, "font_size": 12, "font_color": "#FFFFFF", "bg_color": "#2563EB", "border": 1}
        ),
        "label": workbook.add_format({"bold": True, "bg_color": "#E8EEF7", "border": 1}),
        "value": workbook.add_format({"border": 1}),
        "number": workbook.add_format({"border": 1, "num_format": "0.00"}),
        "integer": workbook.add_format({"border": 1, "num_format": "0"}),
        "percent": workbook.add_format({"border": 1, "num_format": "0.0%"}),
        "wrap": workbook.add_format({"border": 1, "text_wrap": True, "valign": "top"}),
        "pass": workbook.add_format({"bg_color": "#DCFCE7", "font_color": "#166534", "bold": True}),
        "fail": workbook.add_format({"bg_color": "#FEE2E2", "font_color": "#991B1B", "bold": True}),
        "skip": workbook.add_format({"bg_color": "#FEF3C7", "font_color": "#92400E", "bold": True}),
        "high": workbook.add_format({"bg_color": "#FEE2E2", "font_color": "#991B1B", "bold": True}),
        "medium": workbook.add_format({"bg_color": "#FEF3C7", "font_color": "#92400E", "bold": True}),
        "low": workbook.add_format({"bg_color": "#E0F2FE", "font_color": "#075985", "bold": True}),
    }


def _write_summary(
    workbook: xlsxwriter.Workbook,
    formats: Mapping[str, Any],
    receipt: Mapping[str, Any],
    receipt_path: Path,
) -> None:
    sheet = workbook.add_worksheet("Summary")
    sheet.hide_gridlines(2)
    sheet.set_column("A:A", 29)
    sheet.set_column("B:B", 30)
    sheet.set_column("D:H", 20)
    sheet.write("A1", "Multi-Agent Analytics Benchmark Results", formats["title"])
    sheet.write("A3", "Suite overview", formats["section"])
    sheet.merge_range("A3:B3", "Suite overview", formats["section"])
    counts = receipt.get("counts", {}) if isinstance(receipt.get("counts"), Mapping) else {}
    manifest = receipt.get("catalog_manifest", {}) if isinstance(receipt.get("catalog_manifest"), Mapping) else {}
    overview = [
        ("Suite status", "PASS" if receipt.get("passed") else "FAIL"),
        ("Catalog cases", receipt.get("catalog_case_count", 0)),
        ("Route executions", receipt.get("execution_count", 0)),
        ("Passed", counts.get("pass", 0)),
        ("Failed", counts.get("fail", 0)),
        ("Explicit skips", counts.get("skip", 0)),
        ("Semantic fingerprint", receipt.get("result_fingerprint", "")),
        ("Catalog SHA-256", manifest.get("catalog_sha256", "")),
        ("Source receipt", str(receipt_path)),
    ]
    for row, (label, value) in enumerate(overview, start=3):
        sheet.write(row, 0, label, formats["label"])
        sheet.write(row, 1, value, formats["wrap"] if isinstance(value, str) and len(value) > 28 else formats["value"])
    status_format = formats["pass"] if receipt.get("passed") else formats["fail"]
    sheet.write(3, 1, "PASS" if receipt.get("passed") else "FAIL", status_format)

    dimensions = receipt.get("dimension_summary", {}) if isinstance(receipt.get("dimension_summary"), Mapping) else {}
    dim_start = 3
    sheet.merge_range(dim_start - 1, 3, dim_start - 1, 7, "Output-quality dimensions", formats["section"])
    dim_headers = ["Dimension", "Evaluations", "Mean", "Minimum", "Maximum"]
    for column, header in enumerate(dim_headers, start=3):
        sheet.write(dim_start, column, header, formats["label"])
    for index, (name, summary) in enumerate(sorted(dimensions.items()), start=dim_start + 1):
        values = [name, summary.get("count", 0), summary.get("mean"), summary.get("minimum"), summary.get("maximum")]
        for column, value in enumerate(values, start=3):
            sheet.write(index, column, value, formats["number"] if column >= 5 else formats["value"])

    if dimensions:
        chart = workbook.add_chart({"type": "column"})
        first = dim_start + 1
        last = first + len(dimensions) - 1
        chart.add_series(
            {
                "name": "Mean score",
                "categories": ["Summary", first, 3, last, 3],
                "values": ["Summary", first, 5, last, 5],
                "fill": {"color": "#2563EB"},
                "border": {"none": True},
            }
        )
        chart.set_title({"name": "Mean score by quality dimension"})
        chart.set_y_axis({"min": 0, "max": 100, "major_unit": 20})
        chart.set_legend({"none": True})
        chart.set_style(10)
        sheet.insert_chart("D11", chart, {"x_scale": 1.15, "y_scale": 1.05})

    sheet.freeze_panes(3, 0)


def _write_results(workbook: xlsxwriter.Workbook, formats: Mapping[str, Any], receipt: Mapping[str, Any]) -> None:
    sheet = workbook.add_worksheet("Benchmark Results")
    headers = [
        "Benchmark ID",
        "Name",
        "Category",
        "Route",
        "Target",
        "Stages",
        "Status",
        "Duration (ms)",
        "Overall score",
        "Readability",
        "Visibility",
        "Trustworthiness",
        "Executive suitability",
        "Failure kind",
        "Diagnostics",
        "Skipped reason",
        "Artifacts",
    ]
    results = receipt.get("results", []) if isinstance(receipt.get("results"), list) else []
    rows: list[list[Any]] = []
    for item in results:
        if not isinstance(item, Mapping):
            continue
        scores = item.get("scores", {}) if isinstance(item.get("scores"), Mapping) else {}
        rows.append(
            [
                item.get("benchmark_id", ""),
                item.get("name", ""),
                item.get("category", ""),
                item.get("route", ""),
                item.get("target", ""),
                " -> ".join(str(value) for value in item.get("stages", []) or []),
                str(item.get("status", "")).upper(),
                item.get("duration_ms", 0),
                item.get("overall_score"),
                scores.get("readability"),
                scores.get("visibility"),
                scores.get("trustworthiness"),
                scores.get("executive_suitability"),
                item.get("failure_kind", ""),
                " | ".join(str(value) for value in item.get("diagnostics", []) or []),
                item.get("skipped_reason", ""),
                "\n".join(str(value) for value in item.get("artifacts", []) or []),
            ]
        )
    _write_table(sheet, headers, rows, "BenchmarkResults")
    sheet.freeze_panes(1, 0)
    sheet.set_column("A:A", 42)
    sheet.set_column("B:B", 38)
    sheet.set_column("C:E", 24)
    sheet.set_column("F:F", 42)
    sheet.set_column("G:G", 12)
    sheet.set_column("H:M", 17)
    sheet.set_column("N:N", 18)
    sheet.set_column("O:Q", 48, formats["wrap"])
    if rows:
        last_row = len(rows)
        sheet.conditional_format(1, 6, last_row, 6, {"type": "text", "criteria": "containing", "value": "PASS", "format": formats["pass"]})
        sheet.conditional_format(1, 6, last_row, 6, {"type": "text", "criteria": "containing", "value": "FAIL", "format": formats["fail"]})
        sheet.conditional_format(1, 6, last_row, 6, {"type": "text", "criteria": "containing", "value": "SKIP", "format": formats["skip"]})
        sheet.conditional_format(1, 8, last_row, 12, {"type": "3_color_scale", "min_color": "#FECACA", "mid_color": "#FEF3C7", "max_color": "#BBF7D0"})


def _write_improvement_plan(
    workbook: xlsxwriter.Workbook,
    formats: Mapping[str, Any],
    receipt: Mapping[str, Any],
    optimizer_review: Mapping[str, Any],
) -> None:
    sheet = workbook.add_worksheet("Improvement Plan")
    headers = ["Priority", "Item type", "ID", "Route / scope", "Current result", "Issue", "Recommended action", "Verification gate"]
    rows: list[list[Any]] = []
    for item in receipt.get("results", []) if isinstance(receipt.get("results"), list) else []:
        if not isinstance(item, Mapping):
            continue
        scores = item.get("scores", {}) if isinstance(item.get("scores"), Mapping) else {}
        low_dimensions = [f"{name}={float(value):.1f}" for name, value in scores.items() if float(value) < 85.0]
        status = str(item.get("status", "")).lower()
        diagnostics = " | ".join(str(value) for value in item.get("diagnostics", []) or [])
        if status == "pass" and not low_dimensions and not diagnostics:
            continue
        issue = diagnostics or ("Low dimension scores: " + ", ".join(low_dimensions)) or item.get("skipped_reason", "")
        action = _recommended_action(str(item.get("benchmark_id", "")), issue, status)
        rows.append(
            [
                "High" if status == "fail" else "Medium",
                "Workflow benchmark",
                item.get("benchmark_id", ""),
                f"{item.get('route', '')} / {item.get('target', '')}",
                status.upper(),
                issue,
                action,
                "Rerun the failed benchmark, then all 30 route executions without editing frozen expectations.",
            ]
        )

    dispositions = optimizer_review.get("finding_disposition", []) if isinstance(optimizer_review.get("finding_disposition"), list) else []
    for finding in dispositions:
        if not isinstance(finding, Mapping) or finding.get("status") == "closed":
            continue
        status = str(finding.get("status", "open"))
        rows.append(
            [
                "Medium" if status == "open" else "Low",
                "Benchmark assurance",
                finding.get("id", ""),
                "benchmark infrastructure",
                status.upper(),
                finding.get("reason", ""),
                _assurance_action(str(finding.get("id", ""))),
                "Add a deterministic negative/control test and preserve the catalog and semantic fingerprints.",
            ]
        )
    _write_table(sheet, headers, rows, "ImprovementPlan")
    sheet.freeze_panes(1, 0)
    sheet.set_column("A:A", 12)
    sheet.set_column("B:B", 22)
    sheet.set_column("C:C", 44)
    sheet.set_column("D:E", 26)
    sheet.set_column("F:H", 58, formats["wrap"])
    if rows:
        last_row = len(rows)
        for priority, format_name in (("High", "high"), ("Medium", "medium"), ("Low", "low")):
            sheet.conditional_format(1, 0, last_row, 0, {"type": "text", "criteria": "containing", "value": priority, "format": formats[format_name]})


def _write_catalog(workbook: xlsxwriter.Workbook, formats: Mapping[str, Any], receipt: Mapping[str, Any]) -> None:
    sheet = workbook.add_worksheet("Catalog Coverage")
    headers = ["Benchmark ID", "Name", "Category", "Routes", "Stages", "Dimensions", "Hard gates", "Tags", "Target", "Expected behavior"]
    manifest = receipt.get("catalog_manifest", {}) if isinstance(receipt.get("catalog_manifest"), Mapping) else {}
    benchmarks = manifest.get("benchmarks", []) if isinstance(manifest.get("benchmarks"), list) else []
    rows = []
    for item in benchmarks:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            [
                item.get("id", ""),
                item.get("name", ""),
                item.get("category", ""),
                ", ".join(str(value) for value in item.get("routes", []) or []),
                " -> ".join(str(value) for value in item.get("stages", []) or []),
                ", ".join(str(value) for value in item.get("dimensions", []) or []),
                ", ".join(str(value) for value in item.get("hard_gates", []) or []),
                ", ".join(str(value) for value in item.get("tags", []) or []),
                item.get("target", ""),
                json.dumps(item.get("expected", {}), ensure_ascii=False, sort_keys=True),
            ]
        )
    _write_table(sheet, headers, rows, "CatalogCoverage")
    sheet.freeze_panes(1, 0)
    sheet.set_column("A:A", 42)
    sheet.set_column("B:B", 40)
    sheet.set_column("C:D", 22)
    sheet.set_column("E:J", 48, formats["wrap"])


def _write_table(sheet: Any, headers: list[str], rows: list[list[Any]], name: str) -> None:
    for row_index, row in enumerate(rows, start=1):
        for column_index, value in enumerate(row):
            sheet.write(row_index, column_index, value)
    last_row = max(1, len(rows))
    sheet.add_table(
        0,
        0,
        last_row,
        len(headers) - 1,
        {
            "name": name,
            "style": "Table Style Medium 2",
            "columns": [{"header": header} for header in headers],
        },
    )


def _recommended_action(benchmark_id: str, issue: str, status: str) -> str:
    if "actionable_bounded_recommendations" in benchmark_id and "required_action_fields" in issue:
        return (
            "Choose and document one canonical recommendation metric field. Either emit `metric` or formally normalize "
            "`validation_metric` to that contract, then add a schema-level regression test."
        )
    if status == "skip":
        return "Supply the documented prerequisite or create a deterministic fixture; never count an unexecuted skip as a pass."
    if status == "fail":
        return "Repair the producing workflow contract, keep the benchmark fixed, and rerun focused plus full regression checks."
    return "Review the lowest dimension evidence, improve the artifact, and verify the score delta against the frozen catalog."


def _assurance_action(finding_id: str) -> str:
    actions = {
        "B04_HELD_OUT_IS_PUBLIC_AND_MUTABLE": "Add an independently stored, content-addressed held-out set unavailable to runtime prompts and optimizers.",
        "B06_SUBJECTIVE_SCORES_LACK_ANCHORS": "Calibrate blinded raters against anchored examples and record inter-rater agreement.",
        "B08_LEGACY_CROSSWALK_IS_NOT_EQUIVALENCE": "Create criterion-by-criterion mutations proving each legacy gate maps to an executable current check.",
        "B09_ITERATIONS_ARE_NOT_COMPARABLE": "Fingerprint the environment, renderers, evaluators, and fixture oracles in addition to semantic results.",
        "B10_FLAKINESS_POLICY_IS_UNSPECIFIED": "Run the full acceptance suite three times and quarantine or reject unstable semantic fingerprints.",
        "B11_EXECUTIVE_AND_READABILITY_KEYWORD_GAMING": "Add contradictory-priority and hidden-caveat negative controls.",
        "B12_RENDERED_VISIBILITY_COVERAGE_IS_TOO_NARROW": "Render every page and slide across multiple dashboard viewports and filter states.",
        "B13_OPTIMIZER_BOUNDARY_IS_POLICY_ONLY": "Enforce an optimizer path allow-list and require a reviewed diff receipt before acceptance.",
        "B14_SYMBOLIC_FIXTURES_DO_NOT_PROVE_EDGE_CASES": "Content-address every fixture and store separate oracle hashes.",
        "B15_NO_PAIRED_REGRESSION_ACCEPTANCE_RULE": "Add baseline-versus-candidate paired comparison and automatic rollback on any gate regression.",
    }
    return actions.get(finding_id, "Convert the limitation into an executable negative control with a stable oracle and regression test.")


def _read_mapping(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export benchmark results and improvement priorities to Excel.")
    parser.add_argument("receipt", nargs="?", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--optimizer-review", type=Path, default=DEFAULT_OPTIMIZER_REVIEW)
    args = parser.parse_args(list(argv) if argv is not None else None)
    output = args.output or args.receipt.with_suffix(".xlsx")
    try:
        path = export_benchmark_workbook(args.receipt, output, optimizer_review_path=args.optimizer_review)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Excel export failed: {exc}")
        return 2
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
