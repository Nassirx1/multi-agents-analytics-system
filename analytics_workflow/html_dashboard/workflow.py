from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from ..clients import OpenRouterClient
from ..runtime_config import RuntimeConfig
from .contracts import DashboardErrorCode, DashboardWorkflowError
from .events import DashboardEventLogger
from .planning import HTMLDashboardPlanningAgent, normalize_dashboard_plan, safe_project_name
from .renderer import render_dashboard, validate_dashboard_html


StepSetter = Callable[[int, str], None]


class HTMLDashboardWorkflow:
    def __init__(
        self,
        config: RuntimeConfig,
        openrouter_client: OpenRouterClient,
        run_id: str,
        run_dir: Path,
        set_step: StepSetter,
    ) -> None:
        self.config = config
        self.openrouter = openrouter_client
        self.run_id = run_id
        self.run_dir = run_dir.resolve()
        self.dashboard_root = self.run_dir / "dashboard"
        self.dashboard_root.mkdir(parents=True, exist_ok=True)
        self.set_step = set_step
        self.events = DashboardEventLogger(self.dashboard_root / "dashboard_events.jsonl", run_id, config)
        self.planner = HTMLDashboardPlanningAgent(openrouter_client, config.html_dashboard_stage_timeout_seconds)

    def run(self, workflow_state: dict[str, Any]) -> dict[str, Any]:
        csv_data = workflow_state.get("csv_data", {})
        if not isinstance(csv_data, dict) or not csv_data:
            raise DashboardWorkflowError(DashboardErrorCode.MISSING_FIELD, "No loaded datasets are available.")
        total_rows = sum(len(frame) for frame in csv_data.values() if isinstance(frame, pd.DataFrame))
        if total_rows > self.config.html_dashboard_max_rows:
            raise DashboardWorkflowError(
                DashboardErrorCode.TOO_MANY_ROWS,
                f"The HTML dashboard path supports at most {self.config.html_dashboard_max_rows:,} embedded rows; received {total_rows:,}.",
                details={"rows": total_rows, "limit": self.config.html_dashboard_max_rows},
            )

        outputs = workflow_state.setdefault("agent_outputs", {})
        plan = outputs.get("html_dashboard_plan")
        if isinstance(plan, dict):
            plan = normalize_dashboard_plan(plan, csv_data, workflow_state.get("workflow_objective", {}))
            self.set_step(2, "cached")
        else:
            self.set_step(2, "running")
            started = time.perf_counter()
            plan = self.planner.execute(workflow_state)
            self._check_deadline(started, "planning")
            outputs["html_dashboard_plan"] = plan
            self.events.emit("planning", "ok", duration_ms=round((time.perf_counter() - started) * 1000))
            self.set_step(2, "done")

        project_root = confined_path(self.dashboard_root, self.dashboard_root / safe_project_name(str(plan["project_name"])))
        data_root = confined_path(project_root, project_root / "data")
        data_root.mkdir(parents=True, exist_ok=True)
        sources = self._copy_sources(workflow_state, csv_data, data_root)
        _write_json(project_root / "dashboard_plan.json", plan)

        self.set_step(3, "running")
        started = time.perf_counter()
        html_path = confined_path(project_root, project_root / "dashboard.html")
        render_receipt = render_dashboard(plan, csv_data, sources, html_path)
        self._check_deadline(started, "generation")
        _write_json(project_root / "render_receipt.json", render_receipt)
        self.events.emit("generation", "ok", duration_ms=round((time.perf_counter() - started) * 1000), artifact_path=str(html_path))
        self.set_step(3, "done")

        self.set_step(4, "running")
        started = time.perf_counter()
        qa_receipt = validate_dashboard_html(html_path, plan, csv_data)
        self._check_deadline(started, "qa")
        _write_json(project_root / "dashboard_qa_receipt.json", qa_receipt)
        if not qa_receipt.get("passed"):
            self.events.emit("qa", "error", error_code=DashboardErrorCode.QA_FAILED.value, issues=qa_receipt.get("issues", []))
            raise DashboardWorkflowError(
                DashboardErrorCode.QA_FAILED,
                "The generated HTML dashboard failed deterministic validation.",
                details={"issues": qa_receipt.get("issues", [])},
            )
        success_receipt = {
            "completed": True,
            "html": str(html_path),
            "project": str(project_root),
            "self_contained": True,
            "offline": True,
            "external_requests": 0,
            "embedded_rows": total_rows,
            "render_sha256": render_receipt["sha256"],
            "qa": qa_receipt["checks"],
        }
        _write_json(project_root / "dashboard_success_receipt.json", success_receipt)
        self.events.emit("qa", "ok", duration_ms=round((time.perf_counter() - started) * 1000), artifact_path=str(html_path))
        self.set_step(4, "done")
        return {
            "project": str(project_root),
            "html": str(html_path),
            "plan": str(project_root / "dashboard_plan.json"),
            "qa_receipt": str(project_root / "dashboard_qa_receipt.json"),
            "success_receipt": str(project_root / "dashboard_success_receipt.json"),
            "sources": sources,
        }

    def _copy_sources(
        self,
        workflow_state: dict[str, Any],
        csv_data: dict[str, pd.DataFrame],
        data_root: Path,
    ) -> list[dict[str, Any]]:
        entries = {
            str(item.get("name", "")): item
            for item in workflow_state.get("run_manifest", {}).get("datasets", []) or []
            if isinstance(item, dict)
        }
        sources: list[dict[str, Any]] = []
        for name, frame in csv_data.items():
            entry = entries.get(name, {})
            source_path = Path(str(entry.get("path", "")))
            destination = confined_path(data_root, data_root / Path(name).name)
            if source_path.is_file():
                shutil.copy2(source_path, destination)
            else:
                frame.to_csv(destination, index=False)
            sources.append(
                {
                    "name": name,
                    "rows": len(frame),
                    "columns": len(frame.columns),
                    "copy": f"data/{destination.name}",
                    "sha256": _sha256(destination),
                }
            )
        return sources

    def _check_deadline(self, started: float, stage: str) -> None:
        elapsed = time.perf_counter() - started
        if elapsed > self.config.html_dashboard_stage_timeout_seconds:
            raise DashboardWorkflowError(
                DashboardErrorCode.STAGE_TIMEOUT,
                f"HTML dashboard {stage} exceeded {self.config.html_dashboard_stage_timeout_seconds} seconds.",
                retryable=True,
            )


def confined_path(root: Path, candidate: Path) -> Path:
    root_resolved = root.resolve()
    resolved = candidate.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise DashboardWorkflowError(DashboardErrorCode.PATH_OUTSIDE_RUN, f"Path is outside active dashboard root: {resolved}")
    return resolved


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
