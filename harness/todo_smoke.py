#!/usr/bin/env python3
"""Unscored smoke test: run selected tasks once per server and print steps (harness debugging only)."""
import asyncio, sys
from todo_env import load_env, load_state, redact
from todo_score import run_task
async def main(servers, tasks):
    env, state = load_env(), load_state()
    for sid in servers:
        for t in tasks:
            r = await run_task(sid, t, 0, state)
            print(f"{sid:9} {t:22} {'PASS' if r['ok'] else 'FAIL'}  {redact(r['error'] or '', env)[:230]}")
            for st in r["steps"]: print(f"{'':33}- {redact(st, env)[:230]}")
asyncio.run(main(sys.argv[1].split(","), sys.argv[2].split(",")))
