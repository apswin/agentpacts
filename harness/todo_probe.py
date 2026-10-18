#!/usr/bin/env python3
"""Call one tool on one todo server and print the (redacted) response. For setup and debugging.
  uv run --with "mcp>=1.10,<2" python todo_probe.py trello get_lists '{"boardId": "..."}'
"""

import asyncio
import json
import sys

from todo_env import load_env, redact, result_text, session_for


async def main(server_id: str, tool: str, args: dict) -> None:
    env = load_env()
    async with session_for(server_id) as s:
        r = await s.call_tool(tool, args)
        print(f"isError={r.isError}")
        print(redact(result_text(r), env)[:4000])
        sc = getattr(r, "structuredContent", None)
        if sc:
            print("structured:", redact(json.dumps(sc)[:2000], env))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}))
