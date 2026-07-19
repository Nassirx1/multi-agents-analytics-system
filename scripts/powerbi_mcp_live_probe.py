from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect local Power BI Desktop instances exposed by Modeling MCP.")
    parser.add_argument("--command", required=True)
    parser.add_argument("--tool", default="connection_operations")
    parser.add_argument("--operation", default="")
    parser.add_argument("--desktop-title", default="")
    parser.add_argument("--dax-query", default="")
    parser.add_argument("--measure-name", default="")
    parser.add_argument("--reference", action="append", default=[])
    parser.add_argument("--definition-name", default="")
    parser.add_argument("--expression", default="")
    parser.add_argument(
        "--request-json",
        default='{"operation":"ListLocalInstances"}',
        help="JSON object passed as the Modeling MCP request payload.",
    )
    parser.add_argument("--readwrite", action="store_true")
    args = parser.parse_args()
    request = {"operation": args.operation} if args.operation else json.loads(args.request_json)
    if args.reference:
        request["References"] = [{"Name": value} for value in args.reference]
    if args.definition_name:
        request["Definitions"] = [
            {"Name": args.definition_name, "Expression": args.expression, "Kind": "M"}
        ]
    if args.dax_query:
        request = {
            "operation": args.operation or "Execute",
            "Query": args.dax_query,
            "TimeoutSeconds": 60,
            "MaxRows": 20,
        }
    elif args.measure_name:
        escaped_name = args.measure_name.replace("]", "]]" )
        request = {
            "operation": args.operation or "Execute",
            "Query": f'EVALUATE ROW("Value", [{escaped_name}])',
            "TimeoutSeconds": 60,
            "MaxRows": 20,
        }
    if not isinstance(request, dict):
        parser.error("--request-json must decode to a JSON object")
    asyncio.run(
        _probe(
            args.command,
            args.tool,
            request,
            readwrite=args.readwrite,
            desktop_title=args.desktop_title,
        )
    )
    return 0


async def _probe(
    command: str,
    tool: str,
    request: dict[str, object],
    *,
    readwrite: bool,
    desktop_title: str,
) -> None:
    params = StdioServerParameters(
        command=command,
        args=["--start", "--readwrite" if readwrite else "--readonly", "--skipconfirmation"],
        env=dict(os.environ),
    )
    async with stdio_client(params) as streams:
        async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=120)) as session:
            await session.initialize()
            if desktop_title:
                local = await session.call_tool(
                    "connection_operations",
                    {"request": {"operation": "ListLocalInstances"}},
                )
                local_text = "\n".join(
                    str(item.text)
                    for item in getattr(local, "content", []) or []
                    if getattr(item, "text", None)
                )
                local_payload = json.loads(local_text or "{}")
                matches = [
                    item
                    for item in local_payload.get("data", []) or []
                    if str(item.get("parentWindowTitle", "")).casefold() == desktop_title.casefold()
                ]
                if len(matches) != 1:
                    raise RuntimeError(f"Expected one Desktop instance titled {desktop_title!r}; found {len(matches)}")
                await session.call_tool(
                    "connection_operations",
                    {
                        "request": {
                            "operation": "Connect",
                            "ConnectionString": matches[0]["connectionString"],
                        }
                    },
                )
            result = await session.call_tool(
                tool,
                {"request": request},
            )
            values = [str(item.text) for item in getattr(result, "content", []) or [] if getattr(item, "text", None)]
            if values:
                print("\n".join(values))
            else:
                payload = getattr(result, "structuredContent", {}) or {}
                if not payload and hasattr(result, "model_dump"):
                    payload = result.model_dump(mode="json")
                print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
