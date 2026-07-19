from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from analytics_workflow.clients import OpenRouterClient
from analytics_workflow.evidence import build_evidence_bundle, validate_and_sanitize_workflow
from analytics_workflow.presentation_backends import (
    PowerPointMCPBackend,
    enrich_deck_executive_copy,
    inspect_presentation,
)
from analytics_workflow.runtime_config import load_runtime_config
from analytics_workflow.slides import build_deck_spec
from analytics_workflow.slides.deck_spec import DeckSpec


def main() -> int:
    parser = argparse.ArgumentParser(description="Rerun Agent 9 against a completed runtime evidence bundle.")
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--record-existing", action="store_true")
    parser.add_argument("--rebuild-plan", action="store_true")
    args = parser.parse_args()
    run_dir = args.run_directory.resolve()
    prior_deck = DeckSpec.from_any(json.loads((run_dir / "slide_plan.json").read_text(encoding="utf-8")))
    deck = prior_deck
    if args.record_existing:
        payload = json.loads((run_dir / "agent9_mcp_rerun_result.json").read_text(encoding="utf-8"))
        inspection = inspect_presentation(str(payload.get("path", "")))
        rendered = [str(path) for path in payload.get("rendered_files", []) if Path(path).is_file()]
        accepted = (
            payload.get("backend") == "powerpoint_mcp"
            and inspection.valid
            and inspection.slide_count == 12
            and len(rendered) >= 12
        )
        if accepted:
            _record_verified_manifest(run_dir, payload)
        print(json.dumps({"recorded": accepted, "slide_count": inspection.slide_count, "rendered": len(rendered)}, indent=2))
        return 0 if accepted else 1
    agent_outputs = json.loads((run_dir / "agent_outputs.json").read_text(encoding="utf-8"))
    analysis = json.loads((run_dir / "analysis_results.json").read_text(encoding="utf-8"))
    if args.rebuild_plan:
        manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        inputs = manifest.get("workflow_inputs", {}) or {}
        workflow_state = {
            "agent_outputs": agent_outputs,
            "analysis_results": analysis,
            "saved_figures": analysis.get("figures_generated", []) or [],
            "analysis_artifact_warnings": [],
            "user_data_description": inputs.get("user_data_description", ""),
            "decision_tree_target_column": inputs.get("decision_tree_target_column", ""),
            "workflow_objective": {"limitations": []},
            "run_manifest": manifest,
        }
        evidence_path = run_dir / "evidence_bundle.json"
        quality_path = run_dir / "quality_receipt.json"
        if evidence_path.is_file():
            workflow_state["evidence_bundle"] = json.loads(evidence_path.read_text(encoding="utf-8"))
            workflow_state["quality_receipt"] = (
                json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.is_file() else {}
            )
        else:
            workflow_state["evidence_bundle"] = build_evidence_bundle(workflow_state)
            validate_and_sanitize_workflow(workflow_state)
        agent_outputs = workflow_state["agent_outputs"]
        analysis = workflow_state["analysis_results"]
        if not evidence_path.is_file():
            evidence_path.write_text(
                json.dumps(workflow_state["evidence_bundle"], indent=2, default=str), encoding="utf-8"
            )
            quality_path.write_text(
                json.dumps(workflow_state["quality_receipt"], indent=2, default=str), encoding="utf-8"
            )
        (run_dir / "agent_outputs.json").write_text(json.dumps(agent_outputs, indent=2, default=str), encoding="utf-8")
        (run_dir / "analysis_results.json").write_text(json.dumps(analysis, indent=2, default=str), encoding="utf-8")
        deck = build_deck_spec(workflow_state)
        deck.dataset_context = prior_deck.dataset_context
        prior_polish = prior_deck.metadata.get("agent9_copy_polish")
        if isinstance(prior_polish, dict) and int(prior_polish.get("slides_edited", 0) or 0) >= 12:
            deck.metadata["agent9_copy_polish"] = prior_polish
        (run_dir / "slide_plan.json").write_text(
            json.dumps(deck.to_dict(), indent=2, default=str), encoding="utf-8"
        )
    enrich_deck_executive_copy(deck, agent_outputs)
    saved_figures = [
        str(Path(path).resolve())
        for path in (analysis.get("figures_generated", []) or [])
        if Path(path).is_file()
    ]
    config = load_runtime_config()
    client = OpenRouterClient(
        config.openrouter_api_key,
        config.model_name,
        request_timeout_seconds=config.agent_request_timeout_seconds,
        code_loop_timeout_seconds=config.code_loop_request_timeout_seconds,
    )
    backend = PowerPointMCPBackend(
        client,
        command=config.powerpoint_mcp_command,
        timeout_seconds=config.presentation_agent_timeout_seconds,
        request_timeout_seconds=config.agent_request_timeout_seconds,
    )
    output = (args.output or (run_dir / "analytics_report_mcp_verified.pptx")).resolve()
    result = backend.render(deck, str(output), workflow_state={"saved_figures": saved_figures})
    inspection = inspect_presentation(result, expected_deck=deck)
    inspection.rendered_files.extend(backend.last_rendered_files)
    payload = {"backend": backend.name, **inspection.to_dict()}
    accepted = inspection.valid and inspection.slide_count == 12 and len(inspection.rendered_files) >= 12
    if accepted:
        main_deck = run_dir / "analytics_report.pptx"
        if Path(result).resolve() != main_deck.resolve():
            shutil.copy2(result, main_deck)
        payload["main_deck"] = str(main_deck.resolve())
        (run_dir / "slide_plan.json").write_text(
            json.dumps(deck.to_dict(), indent=2, default=str), encoding="utf-8"
        )
        _refresh_consistency_receipt(run_dir, deck)
        _record_verified_manifest(run_dir, payload)
    (run_dir / "agent9_mcp_rerun_result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if accepted else 1


def _record_verified_manifest(run_dir: Path, payload: dict[str, object]) -> None:
    manifest_path = run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["presentation_backend_used"] = "powerpoint_mcp"
    log = manifest.setdefault("presentation_backend_log", [])
    log.append(
        {
            "backend": "powerpoint_mcp",
            "status": "success",
            "source": "verified_agent9_rerun",
            "deck_path": payload.get("main_deck") or payload.get("path"),
            "slide_count": payload.get("slide_count"),
            "rendered_files": payload.get("rendered_files", []),
            "issues": payload.get("issues", []),
        }
    )
    warning = "The original Step 9 MCP attempt used Python fallback; a later verified Agent 9 rerun replaced the main deck with a PowerPoint MCP result."
    warnings = manifest.setdefault("warnings", [])
    if warning not in warnings:
        warnings.append(warning)
    manifest["authoritative_presentation"] = {
        "backend": "powerpoint_mcp",
        "path": payload.get("main_deck") or payload.get("path"),
        "slide_count": payload.get("slide_count"),
        "valid": payload.get("valid"),
        "issues": payload.get("issues", []),
    }
    verified_path = Path(str(payload.get("path") or ""))
    main_path = Path(str(payload.get("main_deck") or ""))
    if verified_path.is_file():
        history_dir = run_dir / "history" / "presentations"
        history_dir.mkdir(parents=True, exist_ok=True)
        archived = history_dir / verified_path.name
        if verified_path.resolve() != archived.resolve():
            shutil.copy2(verified_path, archived)
        manifest["authoritative_presentation"]["history_copy"] = str(archived)
    if main_path.is_file():
        delivery_dir = run_dir / "delivery"
        delivery_dir.mkdir(exist_ok=True)
        shutil.copy2(main_path, delivery_dir / "analytics_report.pptx")
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")


def _refresh_consistency_receipt(run_dir: Path, deck: DeckSpec) -> None:
    evidence_path = run_dir / "evidence_bundle.json"
    report_path = run_dir / "report_outline.json"
    if not evidence_path.is_file():
        return
    evidence_hash = str(json.loads(evidence_path.read_text(encoding="utf-8")).get("bundle_hash", ""))
    pdf_hash = ""
    if report_path.is_file():
        pdf_hash = str(json.loads(report_path.read_text(encoding="utf-8")).get("evidence_bundle_hash", ""))
    slide_hash = str((deck.metadata or {}).get("evidence_bundle_hash", ""))
    mismatches = [
        label
        for label, value in (("pdf", pdf_hash), ("slides", slide_hash))
        if evidence_hash and value != evidence_hash
    ]
    receipt = {
        "status": "failed" if mismatches else "passed",
        "expected_evidence_bundle_hash": evidence_hash,
        "artifact_hashes": {"pdf": pdf_hash, "slides": slide_hash},
        "mismatches": mismatches,
    }
    (run_dir / "final_output_consistency.json").write_text(
        json.dumps(receipt, indent=2), encoding="utf-8"
    )
    if mismatches:
        raise RuntimeError("Agent 9 rerun would create a PDF/slide evidence mismatch: " + ", ".join(mismatches))


if __name__ == "__main__":
    raise SystemExit(main())
