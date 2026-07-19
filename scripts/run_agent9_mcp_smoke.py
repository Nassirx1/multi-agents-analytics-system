from __future__ import annotations

import argparse
import json
from pathlib import Path

from analytics_workflow.clients import OpenRouterClient
from analytics_workflow.presentation_backends import HYBRID_SLIDE_OBJECTIVES, PowerPointMCPBackend, inspect_presentation
from analytics_workflow.runtime_config import load_runtime_config
from analytics_workflow.slides.deck_spec import ContentBlock, DeckSpec, SlideSpec, VisualSpec


def _block(*items: str) -> ContentBlock:
    return ContentBlock(type="bullets", items=list(items))


def _chart(title: str, takeaway: str, data: list[dict[str, object]]) -> VisualSpec:
    return VisualSpec(
        type="structured_chart", chart_type="column", title=title, takeaway=takeaway,
        x="stage", y="value", x_label="Stage", y_label="Control score", data=data,
    )


def _smoke_deck() -> DeckSpec:
    """Twelve stable objectives with structured, replaceable analytical evidence."""
    slides = [
        SlideSpec(1, "title", "title_cover", "Agent 9 converts evidence into an executive decision story",
                  "Hybrid generation combines a complete MCP scaffold with DeepSeek-directed enhancement.",
                  subtitle="PowerPoint MCP preferred | deterministic QA | Python fallback"),
        SlideSpec(2, "data_understanding", "data_understanding_overview",
                  "The executive answer rests on three presentation controls",
                  "A complete scaffold, focused two-slide batches, and deterministic QA contain failure risk.",
                  content_blocks=[_block("All 12 slides exist before model work", "Only Agent 9 receives PowerPoint tools", "Every slide is rendered and reopened")],
                  metrics=[{"label": "Slides", "value": "12"}, {"label": "Batch", "value": "2"}]),
        SlideSpec(3, "market_context", "market_context_bullets",
                  "Native PowerPoint remains the expected executive delivery format",
                  "Editable charts and familiar Office workflows make MCP valuable when reliability is controlled.",
                  content_blocks=[_block("Desktop PowerPoint provides native editing", "Consulting layouts improve scanability", "Fallback protects delivery continuity")]),
        SlideSpec(4, "analysis", "chart_left_insight_right",
                  "The hybrid lifecycle raises control coverage at every stage",
                  "Controls expand from input validation through rendered-slide QA.",
                  visual=_chart("Control coverage by stage", "Post-generation QA applies the broadest checks.",
                                [{"stage": "Input", "value": 3}, {"stage": "Build", "value": 7}, {"stage": "QA", "value": 10}]),
                  content_blocks=[_block("Validate inputs", "Build a populated scaffold", "Inspect and render the final deck")]),
        SlideSpec(5, "analysis", "distribution_with_callout",
                  "Two-slide batches reduce context pressure without fragmenting the story",
                  "Each batch uses one retained PowerPoint session and the same deck-wide limits.",
                  visual=_chart("Slides per enhancement batch", "Focused batches keep tool payloads bounded.",
                                [{"stage": "Batch 1", "value": 2}, {"stage": "Batch 2", "value": 2}, {"stage": "Batch 3", "value": 2}]),
                  content_blocks=[_block("Current-pair specifications only", "No file or slide recreation", "Shared 15-minute deadline")]),
        SlideSpec(6, "analysis", "chart_right_insight_left",
                  "The scaffold removes the one-blank-slide failure mode",
                  "Titles, messages, content blocks, numbering, and chart evidence exist before DeepSeek edits.",
                  visual=_chart("Baseline completeness", "The model starts from a content-bearing deck.",
                                [{"stage": "Old start", "value": 1}, {"stage": "Hybrid start", "value": 12}]),
                  content_blocks=[_block("Stable slide count", "Dataset-specific content", "Renderer-owned geometry")]),
        SlideSpec(7, "analysis", "chart_right_insight_left",
                  "Decision-tree rules make model evidence interpretable",
                  "Verified branches must be separated from descriptive EDA and accompanied by model caveats.",
                  visual=VisualSpec(
                      type="structured_chart", chart_type="decision_tree",
                      title="Illustrative verified decision rule", takeaway="The root split directs two distinct action paths.",
                      data={"root": "QA completeness >= 90%", "left": "Repair before release", "right": "Proceed to visual review"},
                  ),
                  content_blocks=[_block("Show the target and root split", "Report branch outcomes", "State accuracy, imbalance, and scope caveats")]),
        SlideSpec(8, "recommendations", "recommendation_priority",
                  "Adopt the hybrid MCP path with explicit monitoring and ownership",
                  "Use the scaffold as the default baseline and DeepSeek as a bounded enhancement layer.",
                  content_blocks=[_block("Now: enable MCP-preferred Agent 9", "Next: monitor repair and fallback rates", "Ongoing: add templates only for recurring stories")]),
        SlideSpec(9, "recommendations", "recommendation_priority",
                  "A phased rollout assigns ownership and measurable quality gates",
                  "Implementation moves from controlled enablement to monitored scale-up.",
                  content_blocks=[_block("Phase 1 - Platform owner enables MCP and fallback", "Phase 2 - Analytics lead reviews render and repair rates", "Phase 3 - Governance owner approves reusable templates")],
                  metrics=[{"label": "Owner", "value": "Platform"}, {"label": "Guardrail", "value": "QA pass"}]),
        SlideSpec(10, "limitations", "limitations_professional",
                  "Automation improves consistency but does not eliminate environment limits",
                  "PowerPoint, COM, provider availability, and source quality remain explicit dependencies.",
                  content_blocks=[_block("Desktop PowerPoint and .NET are required", "Visual heuristics do not replace human judgment", "Locked files and unsupported objects may force fallback")]),
        SlideSpec(11, "sources", "limitations_professional",
                  "Sources and conclusions define the next executive decision",
                  "Acceptance requires traceable implementation, runtime, and rendered-slide evidence.",
                  content_blocks=[_block("Implementation: presentation_backends.py", "Skill: build-consulting-pptx", "Server: PptMcp.McpServer 1.0.3", "Evidence: python-pptx inspection and rendered slides")]),
        SlideSpec(12, "ending", "executive_summary_closing",
                  "The hybrid Agent 9 path is ready for the final decision",
                  "Proceed only when the 12-slide deck and all rendered images pass QA.",
                  content_blocks=[_block("Decision: approve, refine, or reject", "Owner: analytics platform lead", "Next step: review the rendered executive deck")]),
    ]
    return DeckSpec(
        deck_title="Agent 9 Hybrid Executive Analytics Brief",
        subtitle="Twelve predefined objectives with dataset-specific content",
        audience="Executive leadership", theme="consulting_minimal",
        metadata={"test_type": "end_to_end_smoke", "objectives": list(HYBRID_SLIDE_OBJECTIVES)},
        slides=slides,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the real 12-slide hybrid Agent 9 MCP smoke test.")
    parser.add_argument("--output", default=".powerpoint-mcp-smoke/agent9_hybrid_12_slide_smoke.pptx")
    args = parser.parse_args()
    config = load_runtime_config()
    client = OpenRouterClient(config.openrouter_api_key, config.model_name,
                              request_timeout_seconds=config.agent_request_timeout_seconds,
                              code_loop_timeout_seconds=config.code_loop_request_timeout_seconds)
    backend = PowerPointMCPBackend(client, command=config.powerpoint_mcp_command,
                                   timeout_seconds=config.presentation_agent_timeout_seconds,
                                   request_timeout_seconds=config.agent_request_timeout_seconds)
    deck = _smoke_deck()
    output = Path(args.output).resolve()
    result = backend.render(deck, str(output), workflow_state={"saved_figures": []})
    inspection = inspect_presentation(result, expected_deck=deck)
    inspection.rendered_files.extend(backend.last_rendered_files)
    print(json.dumps(inspection.to_dict(), indent=2))
    return 0 if inspection.valid and inspection.slide_count == 12 and len(inspection.rendered_files) >= 12 else 1


if __name__ == "__main__":
    raise SystemExit(main())
