#!/usr/bin/env python3
"""Print every tool (name + input schema) each configured server exposes.

Use this instead of guessing tool/arg names for servers.json:
  uv run --with mcp python list_tools.py --config servers.json [--only sanzaru]
"""

import argparse
import asyncio
import json
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def list_one(cfg: dict) -> None:
    env = {**os.environ, **cfg.get("env", {})}
    params = StdioServerParameters(command=cfg["command"], args=cfg["args"],
                                   env=env, cwd=cfg.get("cwd"))
    print(f"\n=== {cfg['id']} ===")
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=60)
                tools = await session.list_tools()
                for t in tools.tools:
                    props = (t.inputSchema or {}).get("properties", {})
                    req = (t.inputSchema or {}).get("required", [])
                    print(f"  {t.name}({', '.join(p + ('*' if p in req else '') for p in props)})")
    except Exception as e:  # noqa: BLE001
        print(f"  FAILED: {type(e).__name__}: {e}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    with open(args.config) as f:
        cfg = json.load(f)
    for s in cfg["servers"]:
        if args.only and s["id"] != args.only:
            continue
        await list_one(s)


if __name__ == "__main__":
    asyncio.run(main())
