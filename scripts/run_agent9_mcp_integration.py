from __future__ import annotations

import argparse
import json
from pathlib import Path

from analytics_workflow.clients import OpenRouterClient
from analytics_workflow.presentation_backends import PowerPointMCPBackend, inspect_presentation
from analytics_workflow.runtime_config import load_runtime_config
from analytics_workflow.slides.deck_spec import ContentBlock, DeckSpec, SlideSpec


def _integration_deck() -> DeckSpec:
    return DeckSpec(
        deck_title="Agent 9 MCP Integration",
        subtitle="DeepSeek-directed PowerPoint tool execution",
        audience="Executive",
        theme="consulting_minimal",
        slides=[
            SlideSpec(
                slide_number=1,
                slide_role="title",
                template="title_cover",
                headline="Agent 9 converts analytical evidence into executive decisions",
                main_message="Live integration proof: DeepSeek chooses allow-listed PowerPoint MCP tools.",
            ),
            SlideSpec(
                slide_number=2,
                slide_role="findings",
                template="three_finding_cards",
                headline="Three controls make presentation generation reliable",
                main_message="Tool isolation, deterministic QA, and automatic fallback protect the workflow.",
                content_blocks=[
                    ContentBlock(
                        type="bullets",
                        items=[
                            "Only Agent 9 receives PowerPoint tools",
                            "Unsafe capabilities and out-of-run paths are blocked",
                            "A bounded repair pass runs before Python fallback",
                        ],
                    )
                ],
            ),
            SlideSpec(
                slide_number=3,
                slide_role="summary",
                template="executive_summary_closing",
                headline="The preferred MCP backend is ready for analytical deck production",
                main_message="PowerPoint MCP is preferred; the deterministic Python renderer remains available.",
            ),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise the real DeepSeek-to-PowerPoint-MCP Agent 9 boundary.")
    parser.add_argument(
        "--output",
        default=".powerpoint-mcp-smoke/agent9_deepseek_mcp_integration.pptx",
    )
    args = parser.parse_args()
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
    output = Path(args.output).resolve()
    result = backend.render(_integration_deck(), str(output), workflow_state={"saved_figures": []})
    inspection = inspect_presentation(result, expected_deck=_integration_deck())
    print(json.dumps(inspection.to_dict(), indent=2))
    return 0 if inspection.valid and inspection.slide_count == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
