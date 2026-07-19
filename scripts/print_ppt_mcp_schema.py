from __future__ import annotations

import asyncio
import json
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    env = dict(os.environ)
    env.setdefault("DOTNET_ROLL_FORWARD", "Major")
    async with stdio_client(StdioServerParameters(command="mcp-ppt", args=[], env=env)) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            tools = await session.list_tools()
            for tool in tools.tools:
                if tool.name in {"shape", "text"}:
                    print(json.dumps({"name": tool.name, "schema": tool.inputSchema}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
