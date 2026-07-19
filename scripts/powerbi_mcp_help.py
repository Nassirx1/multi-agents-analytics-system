from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def main() -> int:
    parser = argparse.ArgumentParser(description="Read one installed Power BI Modeling MCP tool's live Help response.")
    parser.add_argument("definition", type=Path)
    parser.add_argument("tool")
    parser.add_argument("--command", required=True)
    args = parser.parse_args()
    asyncio.run(_read_help(args.command, args.definition.resolve(), args.tool))
    return 0


async def _read_help(command: str, definition: Path, tool: str) -> None:
    params = StdioServerParameters(
        command=command,
        args=["--start", "--readwrite", "--skipconfirmation"],
        env=dict(os.environ),
        cwd=str(definition.parent),
    )
    async with stdio_client(params) as streams:
        async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=120)) as session:
            await session.initialize()
            connected = await session.call_tool(
                "connection_operations",
                {"request": {"operation": "ConnectFolder", "folderPath": str(definition)}},
            )
            if getattr(connected, "isError", False):
                raise RuntimeError(_text(connected))
            response = await session.call_tool(tool, {"request": {"operation": "Help"}})
            print(_text(response))


def _text(result: object) -> str:
    values = [str(item.text) for item in getattr(result, "content", []) or [] if getattr(item, "text", None)]
    if values:
        return "\n".join(values)
    return json.dumps(getattr(result, "structuredContent", None) or {}, indent=2, default=str)


if __name__ == "__main__":
    raise SystemExit(main())
