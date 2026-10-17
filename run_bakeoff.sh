#!/usr/bin/env bash
# One command: score every server on the fixture, write traces, bake scores into the MCP server.
#   ./run_bakeoff.sh                 # all servers
#   ./run_bakeoff.sh hakanai,sanzaru # subset (writes traces/partial.json, does not bake)
set -euo pipefail
ROOT="$HOME/agentpacts"   # space-free symlink to this folder (local-stt-mcp breaks on spaces)
export PATH="$ROOT/.tools/node22/bin:$HOME/.local/bin:$PATH"
cd "$ROOT/harness"

curl -s -m 3 -o /dev/null http://127.0.0.1:2022/ || uvx voice-mode service start whisper

ONLY="${1:-}"
OUT="$ROOT/traces/scores.json"; [ -n "$ONLY" ] && OUT="$ROOT/traces/partial.json"

uv run -q --with "mcp>=1.10,<2" --with jiwer --with whisper-normalizer python score.py \
  --config servers.json --suite suite.json \
  --traces "$ROOT/traces" \
  --out "$OUT" ${ONLY:+--only "$ONLY"} 2>&1 | grep -vE "pydub|re\.match|GIL|SyntaxWarning|split_letters|\[INFO\]|\[Whisper\]|MCP Server running"

# local-stt-mcp writes <audio>.txt next to its input; keep fixture folders clean
find "$ROOT/fixtures" "$ROOT/media" -name "*.m4a.txt" -o -name "*.wav.txt" | while read -r f; do rm -f "$f"; done

if [ -z "$ONLY" ]; then
  cd "$ROOT/mcp-server" && node scripts/bake.mjs "$OUT"
fi
