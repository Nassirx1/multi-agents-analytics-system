from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import queue
import threading
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


ANALYTICS_REPORT = "analytics_report"
HTML_DASHBOARD = "html_dashboard"
SHARED_ROUTE = "shared"
KNOWN_ROUTES = (ANALYTICS_REPORT, HTML_DASHBOARD)


class BenchmarkStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class BenchmarkExecutionContext:
    """Isolated, read-only-by-convention inputs supplied to one executor."""

    benchmark_id: str
    route: str
    target: str
    stages: tuple[str, ...]
    fixture: dict[str, Any]
    expected: dict[str, Any]
    hard_gates: tuple[str, ...]
    dimensions: tuple[str, ...]
    output_dir: Path | None = None


@dataclass
class BenchmarkResult:
    benchmark_id: str
    execution_id: str
    name: str
    category: str
    target: str
    route: str
    stages: list[str]
    status: str
    duration_ms: float
    scores: dict[str, float] = field(default_factory=dict)
    overall_score: float | None = None
    diagnostics: list[str] = field(default_factory=list)
    skipped_reason: str = ""
    failure_kind: str = ""
    error_type: str = ""
    artifacts: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkSuiteResult:
    schema_version: str
    suite_id: str
    started_at: str
    completed_at: str
    duration_ms: float
    passed: bool
    counts: dict[str, int]
    catalog_case_count: int
    execution_count: int
    accounted_benchmark_ids: list[str]
    missing_benchmark_ids: list[str]
    dimension_summary: dict[str, dict[str, Any]]
    route_summary: dict[str, dict[str, int]]
    target_summary: dict[str, dict[str, int]]
    results: list[BenchmarkResult]
    catalog_manifest: dict[str, Any] = field(default_factory=dict)
    result_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["results"] = [item.to_dict() for item in self.results]
        return value


BenchmarkExecutor = Callable[..., Any]


class BenchmarkRunner:
    """Execute a benchmark catalog with complete, fail-closed accounting.

    Executors are ordinary Python callables registered by the catalog case's
    ``target``. They receive ``(case, context)`` (or just ``context``) and
    return a mapping containing at least ``passed`` or ``status``. Execution
    uses a daemon thread so a timed-out evaluator cannot block the suite.
    """

    def __init__(
        self,
        executors: Mapping[str, BenchmarkExecutor] | None = None,
        *,
        default_executor: BenchmarkExecutor | None = None,
        default_timeout_seconds: float = 30.0,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.executors = dict(executors or {})
        self.default_executor = default_executor
        self.default_timeout_seconds = max(0.001, float(default_timeout_seconds))
        self.environ = os.environ if environ is None else environ

    def run(
        self,
        cases: Iterable[Any],
        *,
        include_live: bool = False,
        routes: Iterable[str] | None = None,
        benchmark_ids: Iterable[str] | None = None,
        timeout_seconds: float | None = None,
        output_dir: Path | None = None,
        catalog_manifest: Mapping[str, Any] | None = None,
    ) -> BenchmarkSuiteResult:
        catalog = tuple(cases)
        self._validate_case_identity(catalog)
        requested_routes = _normalize_route_filter(routes)
        requested_ids = {str(item) for item in (benchmark_ids or ()) if str(item)}
        unknown_ids = sorted(requested_ids - {_case_value(case, "id") for case in catalog})
        if unknown_ids:
            raise ValueError(f"Unknown benchmark id(s): {', '.join(unknown_ids)}")

        started_wall = _utc_now()
        started = time.perf_counter()
        results: list[BenchmarkResult] = []
        for case in catalog:
            case_id = _case_value(case, "id")
            case_routes = _case_routes(case)
            if requested_ids and case_id not in requested_ids:
                results.append(self._skip(case, case_routes[0], "filtered by benchmark id"))
                continue

            selected_case_routes = [route for route in case_routes if not requested_routes or route in requested_routes]
            if not selected_case_routes:
                results.append(self._skip(case, case_routes[0], "filtered by route"))
                continue

            for route in selected_case_routes:
                results.append(
                    self._run_one(
                        case,
                        route,
                        include_live=include_live,
                        timeout_override=timeout_seconds,
                        output_dir=output_dir,
                    )
                )

        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        catalog_ids = {_case_value(case, "id") for case in catalog}
        accounted = {result.benchmark_id for result in results}
        missing = sorted(catalog_ids - accounted)
        counts = Counter(result.status for result in results)
        normalized_counts = {status.value: int(counts.get(status.value, 0)) for status in BenchmarkStatus}
        passed = not missing and normalized_counts[BenchmarkStatus.FAIL.value] == 0
        suite_id = _suite_id(started_wall)
        manifest = dict(catalog_manifest or {})
        fingerprint = _semantic_result_fingerprint(results, manifest)
        return BenchmarkSuiteResult(
            schema_version="1.0",
            suite_id=suite_id,
            started_at=started_wall,
            completed_at=_utc_now(),
            duration_ms=duration_ms,
            passed=passed,
            counts=normalized_counts,
            catalog_case_count=len(catalog),
            execution_count=len(results),
            accounted_benchmark_ids=sorted(accounted),
            missing_benchmark_ids=missing,
            dimension_summary=_dimension_summary(results),
            route_summary=_status_summary(results, "route"),
            target_summary=_status_summary(results, "target"),
            results=results,
            catalog_manifest=manifest,
            result_fingerprint=fingerprint,
        )

    def _run_one(
        self,
        case: Any,
        route: str,
        *,
        include_live: bool,
        timeout_override: float | None,
        output_dir: Path | None,
    ) -> BenchmarkResult:
        deterministic = bool(_case_value(case, "deterministic", True))
        credentials = _required_credentials(_case_value(case, "credentials_required", ()))
        if not deterministic and not include_live:
            return self._skip(case, route, "live-only benchmark; rerun with --include-live")
        missing_credentials = [name for name in credentials if not str(self.environ.get(name, "")).strip()]
        if missing_credentials:
            return self._skip(case, route, "missing environment credential(s): " + ", ".join(missing_credentials))

        target = _case_value(case, "target")
        executor = self.executors.get(target) or self.default_executor or _discover_executor(target)
        if executor is None:
            return self._failure(
                case,
                route,
                duration_ms=0.0,
                failure_kind="executor_not_found",
                diagnostics=[f"No executor is registered for target '{target}'."],
            )

        timeout = timeout_override
        if timeout is None:
            timeout = _case_value(case, "timeout_seconds", self.default_timeout_seconds)
        timeout = max(0.001, float(timeout or self.default_timeout_seconds))
        context = BenchmarkExecutionContext(
            benchmark_id=_case_value(case, "id"),
            route=route,
            target=target,
            stages=tuple(str(item) for item in _case_value(case, "stages", ()) or ()),
            fixture=_thaw_catalog_value(_case_value(case, "fixture", {}) or {}),
            expected=_thaw_catalog_value(_case_value(case, "expected", {}) or {}),
            hard_gates=tuple(str(item) for item in _case_value(case, "hard_gates", ()) or ()),
            dimensions=tuple(str(item) for item in _case_value(case, "dimensions", ()) or ()),
            output_dir=output_dir.resolve() if output_dir is not None else None,
        )
        started = time.perf_counter()
        outcome = _call_with_timeout(executor, case, context, timeout)
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        if outcome[0] == "timeout":
            return self._failure(
                case,
                route,
                duration_ms=duration_ms,
                failure_kind="timeout",
                diagnostics=[f"Benchmark exceeded its {timeout:g}-second deadline."],
            )
        if outcome[0] == "error":
            error = outcome[1]
            trace = outcome[2]
            return self._failure(
                case,
                route,
                duration_ms=duration_ms,
                failure_kind="executor_error",
                error_type=type(error).__name__,
                diagnostics=[_redact_text(str(error)), _redact_text(trace)],
            )

        try:
            normalized = _normalize_observation(outcome[1], context)
        except Exception as exc:  # malformed evaluator output must fail closed
            return self._failure(
                case,
                route,
                duration_ms=duration_ms,
                failure_kind="invalid_observation",
                error_type=type(exc).__name__,
                diagnostics=[_redact_text(str(exc))],
            )

        if normalized["status"] == BenchmarkStatus.SKIP.value:
            reason = str(normalized.get("skipped_reason") or "executor returned an explicit skip")
            result = self._skip(case, route, reason, duration_ms=duration_ms)
            result.diagnostics = normalized["diagnostics"]
            result.metadata = normalized["metadata"]
            return result

        route_issues = _route_isolation_issues(route, normalized)
        diagnostics = normalized["diagnostics"] + route_issues
        passed = bool(normalized["passed"]) and not route_issues
        scores = normalized["scores"]
        return BenchmarkResult(
            benchmark_id=context.benchmark_id,
            execution_id=_execution_id(context.benchmark_id, route),
            name=_case_value(case, "name"),
            category=_case_value(case, "category"),
            target=target,
            route=route,
            stages=list(context.stages),
            status=BenchmarkStatus.PASS.value if passed else BenchmarkStatus.FAIL.value,
            duration_ms=duration_ms,
            scores=scores,
            overall_score=normalized.get("overall_score") if normalized.get("overall_score") is not None else _mean(scores.values()),
            diagnostics=diagnostics,
            failure_kind="" if passed else ("route_isolation" if route_issues else str(normalized.get("failure_kind") or "assertion")),
            artifacts=normalized["artifacts"],
            metadata=normalized["metadata"],
        )

    def _skip(
        self,
        case: Any,
        route: str,
        reason: str,
        *,
        duration_ms: float = 0.0,
    ) -> BenchmarkResult:
        benchmark_id = _case_value(case, "id")
        return BenchmarkResult(
            benchmark_id=benchmark_id,
            execution_id=_execution_id(benchmark_id, route),
            name=_case_value(case, "name"),
            category=_case_value(case, "category"),
            target=_case_value(case, "target"),
            route=route,
            stages=list(_case_value(case, "stages", ()) or ()),
            status=BenchmarkStatus.SKIP.value,
            duration_ms=duration_ms,
            skipped_reason=reason,
        )

    def _failure(
        self,
        case: Any,
        route: str,
        *,
        duration_ms: float,
        failure_kind: str,
        diagnostics: Sequence[str],
        error_type: str = "",
    ) -> BenchmarkResult:
        benchmark_id = _case_value(case, "id")
        return BenchmarkResult(
            benchmark_id=benchmark_id,
            execution_id=_execution_id(benchmark_id, route),
            name=_case_value(case, "name"),
            category=_case_value(case, "category"),
            target=_case_value(case, "target"),
            route=route,
            stages=list(_case_value(case, "stages", ()) or ()),
            status=BenchmarkStatus.FAIL.value,
            duration_ms=duration_ms,
            diagnostics=[str(item) for item in diagnostics if str(item)],
            failure_kind=failure_kind,
            error_type=error_type,
        )

    @staticmethod
    def _validate_case_identity(catalog: Sequence[Any]) -> None:
        ids = [_case_value(case, "id") for case in catalog]
        missing = [index for index, value in enumerate(ids) if not value]
        duplicates = sorted(item for item, count in Counter(ids).items() if item and count > 1)
        if missing:
            raise ValueError(f"Catalog cases at indexes {missing} have no id.")
        if duplicates:
            raise ValueError(f"Duplicate benchmark id(s): {', '.join(duplicates)}")


def run_default_catalog(
    *,
    output_dir: Path,
    include_live: bool = False,
    routes: Iterable[str] | None = None,
    benchmark_ids: Iterable[str] | None = None,
    timeout_seconds: float | None = None,
    executors: Mapping[str, BenchmarkExecutor] | None = None,
    environ: Mapping[str, str] | None = None,
) -> BenchmarkSuiteResult:
    from .catalog import benchmark_catalog_manifest, load_benchmark_catalog, validate_benchmark_catalog
    from .reporting import write_benchmark_receipts

    cases = load_benchmark_catalog()
    problems = validate_benchmark_catalog(cases)
    if problems:
        raise ValueError("Invalid benchmark catalog: " + "; ".join(problems))
    manifest = benchmark_catalog_manifest()
    verify_frozen_catalog_manifest(manifest)
    protected_targets = {
        "quality.four_dimension_score",
        "quality.held_out_metamorphic_controls",
    }
    unsafe_overrides = sorted(protected_targets.intersection(executors or {}))
    if unsafe_overrides:
        raise ValueError(
            "Canonical assured evaluator override is forbidden for: " + ", ".join(unsafe_overrides)
        )
    runner = BenchmarkRunner(executors=executors, environ=environ)
    result = runner.run(
        cases,
        include_live=include_live,
        routes=routes,
        benchmark_ids=benchmark_ids,
        timeout_seconds=timeout_seconds,
        output_dir=output_dir,
        catalog_manifest=manifest,
    )
    write_benchmark_receipts(result, output_dir)
    return result


def verify_frozen_catalog_manifest(
    manifest: Mapping[str, Any],
    frozen_path: Path | None = None,
) -> None:
    """Abort acceptance runs when the executable catalog differs from its frozen receipt."""

    path = frozen_path or Path(__file__).resolve().parents[1] / "evals" / "benchmark_catalog.v1.json"
    try:
        frozen = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Frozen benchmark catalog receipt is unavailable or invalid: {path}") from exc
    keys = ("catalog_version", "catalog_sha256", "benchmark_count")
    mismatches = [key for key in keys if frozen.get(key) != manifest.get(key)]
    frozen_pairs = [(item.get("id"), item.get("target")) for item in frozen.get("benchmarks", ())]
    manifest_pairs = [(item.get("id"), item.get("target")) for item in manifest.get("benchmarks", ())]
    if frozen_pairs != manifest_pairs:
        mismatches.append("benchmarks")
    if mismatches:
        raise ValueError(
            "Executable benchmark catalog does not match the frozen receipt: " + ", ".join(mismatches)
        )


def _semantic_result_fingerprint(
    results: Sequence[BenchmarkResult],
    catalog_manifest: Mapping[str, Any],
) -> str:
    """Hash stable verdict facts while excluding clocks, durations, and output paths."""

    payload = {
        "schema_version": "1.0",
        "catalog_sha256": str(catalog_manifest.get("catalog_sha256", "")),
        "executions": [
            {
                "benchmark_id": item.benchmark_id,
                "route": item.route,
                "target": item.target,
                "status": item.status,
                "scores": dict(sorted(item.scores.items())),
                "overall_score": item.overall_score,
                "failure_kind": item.failure_kind,
                "skipped_reason": item.skipped_reason,
            }
            for item in results
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _case_value(case: Any, name: str, default: Any = "") -> Any:
    if isinstance(case, Mapping):
        return case.get(name, default)
    return getattr(case, name, default)


def _thaw_catalog_value(value: Any) -> Any:
    """Copy immutable catalog MappingProxy/tuple structures into executor data."""

    if isinstance(value, Mapping):
        return {str(key): _thaw_catalog_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_catalog_value(item) for item in value]
    if isinstance(value, list):
        return [_thaw_catalog_value(item) for item in value]
    return copy.deepcopy(value)


def _case_routes(case: Any) -> tuple[str, ...]:
    raw = tuple(str(item) for item in (_case_value(case, "routes", ()) or ()))
    expanded: list[str] = []
    for route in raw or (SHARED_ROUTE,):
        normalized = route.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in {"all", "both"}:
            expanded.extend(KNOWN_ROUTES)
        elif normalized in {*KNOWN_ROUTES, SHARED_ROUTE}:
            expanded.append(normalized)
        else:
            raise ValueError(f"Unknown route '{route}' in benchmark {_case_value(case, 'id')!r}.")
    return tuple(dict.fromkeys(expanded))


def _normalize_route_filter(routes: Iterable[str] | None) -> set[str]:
    if routes is None:
        return set()
    result: set[str] = set()
    for route in routes:
        normalized = str(route).strip().lower().replace("-", "_").replace(" ", "_")
        if normalized == "all":
            return set()
        if normalized not in {*KNOWN_ROUTES, SHARED_ROUTE}:
            raise ValueError(f"Unknown route filter: {route}")
        result.add(normalized)
    return result


def _call_with_timeout(
    executor: BenchmarkExecutor,
    case: Any,
    context: BenchmarkExecutionContext,
    timeout_seconds: float,
) -> tuple[str, Any, str]:
    mailbox: queue.Queue[tuple[str, Any, str]] = queue.Queue(maxsize=1)

    def work() -> None:
        try:
            value = _invoke_executor(executor, case, context)
            mailbox.put(("ok", value, ""))
        except BaseException as exc:  # capture executor failures without killing the suite
            mailbox.put(("error", exc, traceback.format_exc(limit=8)))

    thread = threading.Thread(target=work, name=f"benchmark-{context.benchmark_id}", daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        return ("timeout", None, "")
    try:
        return mailbox.get_nowait()
    except queue.Empty:
        return ("error", RuntimeError("Benchmark executor exited without an observation."), "")


def _invoke_executor(executor: BenchmarkExecutor, case: Any, context: BenchmarkExecutionContext) -> Any:
    try:
        parameters = list(inspect.signature(executor).parameters.values())
    except (TypeError, ValueError):
        parameters = []
    positional = [
        item
        for item in parameters
        if item.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    has_varargs = any(item.kind == inspect.Parameter.VAR_POSITIONAL for item in parameters)
    if has_varargs or len(positional) >= 2:
        return executor(case, context)
    return executor(context)


def _discover_executor(target: str) -> BenchmarkExecutor | None:
    """Load optional catalog-owned evaluators without coupling the runner to them."""

    try:
        from . import evaluators  # type: ignore
    except ImportError:
        return None
    getter = getattr(evaluators, "get_benchmark_executor", None)
    if callable(getter):
        value = getter(target)
        return value if callable(value) else None
    registry = getattr(evaluators, "BENCHMARK_EXECUTORS", None) or getattr(evaluators, "EXECUTORS", None)
    if isinstance(registry, Mapping) and callable(registry.get(target)):
        return registry[target]
    return None


def _normalize_observation(value: Any, context: BenchmarkExecutionContext) -> dict[str, Any]:
    if is_dataclass(value):
        value = asdict(value)
    elif hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    if isinstance(value, bool):
        value = {"passed": value}
    if not isinstance(value, Mapping):
        raise TypeError("Executor observation must be a mapping, dataclass, to_dict object, or bool.")
    observation = dict(value)
    raw_status = str(observation.get("status", "")).strip().lower()
    if raw_status in {"skip", "skipped"}:
        status = BenchmarkStatus.SKIP.value
        passed = False
    elif raw_status in {"pass", "passed", "ok", "success"}:
        status = BenchmarkStatus.PASS.value
        passed = True
    elif raw_status in {"fail", "failed", "error", "timeout"}:
        status = BenchmarkStatus.FAIL.value
        passed = False
    elif "passed" in observation:
        passed = bool(observation["passed"])
        status = BenchmarkStatus.PASS.value if passed else BenchmarkStatus.FAIL.value
    else:
        raise ValueError("Executor observation must contain 'passed' or a recognized 'status'.")

    scores = _normalize_scores(observation.get("scores") or observation.get("dimension_scores") or {})
    overall_score = observation.get("overall_score")
    if overall_score is not None:
        try:
            overall_score = round(float(overall_score), 3)
        except (TypeError, ValueError) as exc:
            raise TypeError("overall_score must be numeric.") from exc
        if not 0.0 <= overall_score <= 100.0:
            raise ValueError("overall_score must be between 0 and 100.")
    diagnostics = _string_list(observation.get("diagnostics") or observation.get("issues") or [])
    artifacts = _string_list(observation.get("artifacts") or [])
    metadata = observation.get("metadata") if isinstance(observation.get("metadata"), Mapping) else {}
    gate_results = observation.get("gate_results") if isinstance(observation.get("gate_results"), Mapping) else {}
    missing_gates = sorted(set(context.hard_gates) - {str(name) for name in gate_results})
    failed_gates = sorted(
        str(name)
        for name, gate_value in gate_results.items()
        if str(name) in set(context.hard_gates) and not _gate_passed(gate_value)
    )
    if missing_gates and status != BenchmarkStatus.SKIP.value:
        passed = False
        status = BenchmarkStatus.FAIL.value
        diagnostics.append("Missing required hard-gate result(s): " + ", ".join(missing_gates))
    if failed_gates and status != BenchmarkStatus.SKIP.value:
        passed = False
        status = BenchmarkStatus.FAIL.value
        diagnostics.append("Failed hard gate(s): " + ", ".join(failed_gates))
    required_dimensions = set(context.dimensions)
    missing_dimensions = sorted(required_dimensions - set(scores))
    if missing_dimensions and status != BenchmarkStatus.SKIP.value:
        passed = False
        status = BenchmarkStatus.FAIL.value
        diagnostics.append("Missing required dimension score(s): " + ", ".join(missing_dimensions))
    return {
        "status": status,
        "passed": passed,
        "scores": scores,
        "overall_score": overall_score,
        "diagnostics": [_redact_text(item) for item in diagnostics],
        "artifacts": artifacts,
        "metadata": _json_safe(dict(metadata)),
        "skipped_reason": str(observation.get("skipped_reason") or observation.get("skip_reason") or ""),
        "failure_kind": str(observation.get("failure_kind") or ""),
        "calls": _string_list(observation.get("calls") or observation.get("invocations") or []),
    }


def _gate_passed(value: Any) -> bool:
    if isinstance(value, Mapping):
        value = value.get("passed", value.get("value", False))
    return bool(value)


def _normalize_scores(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise TypeError("scores must be a mapping of dimension names to numbers.")
    result: dict[str, float] = {}
    for name, raw in value.items():
        if isinstance(raw, Mapping):
            raw = raw.get("score", raw.get("value"))
        if isinstance(raw, bool):
            score = 100.0 if raw else 0.0
        else:
            try:
                score = float(raw)
            except (TypeError, ValueError) as exc:
                raise TypeError(f"Score for {name!r} is not numeric.") from exc
        if not 0.0 <= score <= 100.0:
            raise ValueError(f"Score for {name!r} must be between 0 and 100; got {score}.")
        result[str(name)] = round(score, 3)
    return result


def _route_isolation_issues(route: str, observation: Mapping[str, Any]) -> list[str]:
    artifacts = [str(item).lower() for item in observation.get("artifacts", [])]
    calls = [str(item).lower().replace(" ", "_") for item in observation.get("calls", [])]
    issues: list[str] = []
    if route == HTML_DASHBOARD:
        forbidden_extensions = (".pdf", ".ppt", ".pptx", ".pbip", ".pbir")
        forbidden_calls = ("pdf", "powerpoint", "slide_deck", "market_researcher", "data_scientist_coder", "power_bi")
        leaked = [item for item in artifacts if item.endswith(forbidden_extensions)]
        leaked_calls = [item for item in calls if any(token in item for token in forbidden_calls)]
        if leaked:
            issues.append("HTML dashboard route emitted forbidden artifact(s): " + ", ".join(leaked))
        if leaked_calls:
            issues.append("HTML dashboard route invoked forbidden stage(s): " + ", ".join(leaked_calls))
    elif route == ANALYTICS_REPORT:
        leaked = [item for item in artifacts if item.endswith((".pbip", ".pbir")) or item.endswith("dashboard.html")]
        leaked_calls = [item for item in calls if "html_dashboard" in item or "power_bi" in item]
        if leaked:
            issues.append("Analytics report route emitted dashboard/Power BI artifact(s): " + ", ".join(leaked))
        if leaked_calls:
            issues.append("Analytics report route invoked forbidden dashboard stage(s): " + ", ".join(leaked_calls))
    return issues


def _dimension_summary(results: Sequence[BenchmarkResult]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for result in results:
        if result.status == BenchmarkStatus.SKIP.value:
            continue
        for name, score in result.scores.items():
            grouped[name].append(float(score))
    return {
        name: {
            "count": len(values),
            "mean": _mean(values),
            "minimum": round(min(values), 3),
            "maximum": round(max(values), 3),
        }
        for name, values in sorted(grouped.items())
        if values
    }


def _status_summary(results: Sequence[BenchmarkResult], attribute: str) -> dict[str, dict[str, int]]:
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    for result in results:
        grouped[str(getattr(result, attribute))][result.status] += 1
    return {
        key: {status.value: int(counts.get(status.value, 0)) for status in BenchmarkStatus}
        for key, counts in sorted(grouped.items())
    }


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [str(value)]
    try:
        return [str(item) for item in value]
    except TypeError:
        return [str(value)]


def _required_credentials(value: Any) -> tuple[str, ...]:
    if value is True:
        return ("OPENROUTER_API_KEY", "BRAVE_API_KEY")
    if value in (False, None, ""):
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _redact_text(value: str) -> str:
    text = str(value)
    for marker in ("OPENROUTER_API_KEY", "BRAVE_API_KEY"):
        secret = os.environ.get(marker, "")
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[-4000:]


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return round(sum(values) / len(values), 3) if values else None


def _execution_id(benchmark_id: str, route: str) -> str:
    return f"{benchmark_id}::{route}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _suite_id(started_at: str) -> str:
    compact = "".join(character for character in started_at if character.isdigit())[:17]
    return f"benchmark-{compact}"
