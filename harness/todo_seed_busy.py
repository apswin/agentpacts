#!/usr/bin/env python3
"""Seed the persistent 30-item busy-list fixture for each server (idempotent).
The target "Renew passport [busy-target]" is created FIRST (oldest), then 29 fillers.
Todoist and Trello get a separate "AgentPacts Busy" project/list so the basic tasks
keep running on clean containers; Notion and Airtable have one table, shared.
  uv run --with "mcp>=1.10,<2" python todo_seed_busy.py todoist trello notion [airtable]
"""

import asyncio
import sys

from todo_env import load_state, result_text, save_state, session_for
from todo_score import ADAPTERS, BUSY_TARGET, parse_json

BUSY = "AgentPacts Busy"
FILLERS = [f"Filler {k:02d} [busy]" for k in range(1, 30)]


async def seed(server_id, state):
    st = state[server_id]
    async with session_for(server_id) as s:
        if server_id == "todoist" and "busyProjectId" not in st:
            found = parse_json(result_text(await s.call_tool("find-projects", {"searchText": BUSY})))
            proj = next((p for p in found["projects"] if p["name"] == BUSY), None) or \
                parse_json(result_text(await s.call_tool("add-projects", {"projects": [{"name": BUSY}]})))["projects"][0]
            st["busyProjectId"] = proj["id"]
        if server_id == "trello" and "busyListId" not in st:
            lists = parse_json(result_text(await s.call_tool("get_lists", {"boardId": st["boardId"]})))
            l = next((x for x in lists if x["name"] == BUSY and not x["closed"]), None) or \
                parse_json(result_text(await s.call_tool("add_list_to_board", {"boardId": st["boardId"], "name": BUSY})))
            st["busyListId"] = l["id"]
        save_state(state)
        a = ADAPTERS[server_id](s, st)
        # read existing fixture WITHOUT page-size limits so seeding is idempotent
        if server_id == "todoist":
            j = parse_json(result_text(await s.call_tool("find-tasks", {"projectId": st["busyProjectId"], "limit": 100})))
            have = {t["content"] for t in j["tasks"]}
        else:
            have = {t for _, t in await a.busy_items()}
        async def add(title):
            if server_id == "todoist":
                await s.call_tool("add-tasks", {"tasks": [{"content": title, "projectId": st["busyProjectId"]}]})
            elif server_id == "trello":
                await s.call_tool("add_card_to_list", {"boardId": st["boardId"], "listId": st["busyListId"], "name": title})
            else:
                await a.create(title)
        if BUSY_TARGET not in have:
            await add(BUSY_TARGET)
            await asyncio.sleep(1.1)  # make sure the target is strictly the oldest
        missing = [f for f in FILLERS if f not in have]
        for f in missing:
            await add(f)
        print(f"{server_id}: target {'existed' if BUSY_TARGET in have else 'created'}, {len(missing)} fillers added")


async def main(which):
    state = load_state()
    state.setdefault("notion", {})["dueProp"] = "Deadline"
    save_state(state)
    for sid in which:
        await seed(sid, state)
        save_state(state)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
