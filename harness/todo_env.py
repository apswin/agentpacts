"""Shared helpers for the todos-v1 harness: credential loading, server sessions, secret redaction."""

import json
import os
import re
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, "..", "servers", "todos.env")
STATE_PATH = os.path.join(HERE, "..", "servers", "todos.state.json")  # container ids (gitignored)


def load_env() -> dict:
    vals = {}
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
    return vals


def redact(text: str, env: dict) -> str:
    for k, v in env.items():
        if v and len(v) >= 12 and ("TOKEN" in k or "KEY" in k):
            text = text.replace(v, f"<{k}>")
    return text


def server_cfg(server_id: str) -> dict:
    with open(os.path.join(HERE, "todo_servers.json")) as f:
        cfg = next(s for s in json.load(f)["servers"] if s["id"] == server_id)
    env = load_env()
    resolved = {k: re.sub(r"\$\{(\w+)\}", lambda m: env.get(m.group(1), ""), v) for k, v in cfg["env"].items()}
    return {**cfg, "env": resolved}


@asynccontextmanager
async def session_for(server_id: str):
    cfg = server_cfg(server_id)
    node_dir = os.path.dirname(os.path.realpath(os.path.join(HERE, "..", ".tools", "node22", "bin", "node")))
    env = {**os.environ, "PATH": f"{node_dir}:{os.environ.get('PATH', '')}", **cfg["env"]}
    params = StdioServerParameters(command=cfg["command"], args=cfg["args"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def result_text(result) -> str:
    return "\n".join(getattr(b, "text", "") for b in result.content).strip()


def load_state() -> dict:
    return json.load(open(STATE_PATH)) if os.path.exists(STATE_PATH) else {}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
    os.chmod(STATE_PATH, 0o600)
