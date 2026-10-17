#!/usr/bin/env python3
"""Call the AgentPacts MCP server over streamable HTTP, like any remote client would.

  uv run --with "mcp>=1.10,<2" python probe.py http://localhost:3911/mcp
  uv run --with "mcp>=1.10,<2" python probe.py https://<slug>.run.mcp-use.com/mcp
"""

import asyncio
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

CALLS = [
    ("list_categories", {}),
    ("search_tools", {"task": "transcribe a meeting recording"}),
    ("search_tools", {"task": "transcribe a noisy street interview with accents", "local_only": True}),
    ("search_tools", {"task": "transcribe my iPhone voice memo"}),
    ("get_scores", {"server_id": "hakanai"}),
    ("search_tools", {"task": "book a flight to Tokyo"}),
]


async def main(url: str) -> None:
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])
            for name, args in CALLS:
                r = await session.call_tool(name, args)
                print(f"\n----- {name}({args}) error={r.isError}")
                for b in r.content:
                    print(getattr(b, "text", b))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3911/mcp"))
