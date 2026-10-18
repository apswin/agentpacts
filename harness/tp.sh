#!/usr/bin/env bash
# todo probe wrapper: ./tp.sh <server> <tool> '<json args>'
cd "$(dirname "$0")" && PATH="$HOME/.local/bin:$PATH" uv run -q --with "mcp>=1.10,<2" python todo_probe.py "$@" 2>&1 | grep -v "Process group termination\|^Server started"
