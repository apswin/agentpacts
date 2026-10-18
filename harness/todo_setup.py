#!/usr/bin/env python3
"""One-time setup of dedicated test containers for todos-v1 (idempotent).
Containers are created through each server's own MCP tools where it has one;
Notion's database is created via the REST API because the MCP server has no
create-database tool. Ids are saved to servers/todos.state.json (gitignored).
  uv run --with "mcp>=1.10,<2" python todo_setup.py [todoist trello notion airtable]
"""

import asyncio
import json
import re
import sys
import urllib.request

from todo_env import load_env, load_state, result_text, save_state, session_for

PROJECT = "AgentPacts Test"
DONE = "AgentPacts Done"


def parse_json(text: str):
    i = min([p for p in (text.find("{"), text.find("[")) if p >= 0])
    return json.JSONDecoder().raw_decode(text[i:])[0]


async def setup_todoist(state, env):
    async with session_for("todoist") as s:
        found = parse_json(result_text(await s.call_tool("find-projects", {"searchText": PROJECT})))
        proj = next((p for p in found["projects"] if p["name"] == PROJECT), None)
        if not proj:
            r = parse_json(result_text(await s.call_tool("add-projects", {"projects": [{"name": PROJECT}]})))
            proj = r["projects"][0]
    state["todoist"] = {"projectId": proj["id"]}


async def setup_trello(state, env):
    board = env["TRELLO_BOARD_ID"]
    async with session_for("trello") as s:
        lists = parse_json(result_text(await s.call_tool("get_lists", {"boardId": board})))
        ids = {}
        for name in (PROJECT, DONE):
            l = next((x for x in lists if x["name"] == name and not x["closed"]), None)
            if not l:
                l = parse_json(result_text(await s.call_tool("add_list_to_board", {"boardId": board, "name": name})))
            ids[name] = l["id"]
    state["trello"] = {"boardId": board, "listId": ids[PROJECT], "doneListId": ids[DONE]}


async def setup_notion(state, env):
    if state.get("notion", {}).get("dataSourceId"):
        return
    page = re.findall(r"[0-9a-f]{32}", env["NOTION_PARENT_PAGE_URL"].replace("-", ""))[-1]
    body = {
        "parent": {"type": "page_id", "page_id": page},
        "title": [{"type": "text", "text": {"content": PROJECT}}],
        "initial_data_source": {"properties": {
            "Name": {"title": {}},
            "Status": {"select": {"options": [{"name": "Todo"}, {"name": "Done"}]}},
        }},
    }
    req = urllib.request.Request(
        "https://api.notion.com/v1/databases", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {env['NOTION_TOKEN']}", "Notion-Version": "2025-09-03",
                 "Content-Type": "application/json"})
    db = json.load(urllib.request.urlopen(req))
    state["notion"] = {"databaseId": db["id"], "dataSourceId": db["data_sources"][0]["id"]}


async def setup_airtable(state, env):
    base = env["AIRTABLE_BASE_ID"]
    async with session_for("airtable") as s:
        r = await s.call_tool("list_tables", {"baseId": base, "detailLevel": "tableIdentifiersOnly"})
        if r.isError:
            raise RuntimeError("airtable list_tables failed (token access?)")
        tables = parse_json(result_text(r))
        tables = tables.get("tables", tables) if isinstance(tables, dict) else tables
        t = next((x for x in tables if x["name"] == "Tasks"), None)
        if not t:
            t = parse_json(result_text(await s.call_tool("create_table", {"baseId": base, "name": "Tasks", "fields": [
                {"name": "Title", "type": "singleLineText"},
                {"name": "Status", "type": "singleSelect", "options": {"choices": [{"name": "Todo"}, {"name": "Done"}]}},
                {"name": "Due", "type": "dateTime", "options": {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"},
                                                               "timeZone": "America/Los_Angeles"}},
            ]})))
    state["airtable"] = {"baseId": base, "tableId": t["id"]}


async def main(which):
    env, state = load_env(), load_state()
    for name in which:
        try:
            await {"todoist": setup_todoist, "trello": setup_trello, "notion": setup_notion,
                   "airtable": setup_airtable}[name](state, env)
            print(f"{name}: ok {sorted(state[name])}")
        except Exception as e:  # noqa: BLE001
            print(f"{name}: FAILED {type(e).__name__}: {str(e)[:200]}")
        save_state(state)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["todoist", "trello", "notion", "airtable"]))
