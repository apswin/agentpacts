#!/usr/bin/env python3
"""
AgentPacts todos-v1 bake-off: pass/fail scoring of todo MCP servers.

Five tasks, identical intent, server-specific tool calls. The harness is the
fixed agent: the same sequence of operations for every server, no LLM in the
loop. Every check reads state back through the SAME MCP server.

  create    add "Buy milk [run-N]"           -> it is listed with the exact title
  list      (setup: create) list the project -> the item appears
  update    (setup: create) rename to "Buy oat milk [run-N]" -> new title listed, old gone
  complete  (setup: create) mark done        -> server reports it complete
  delete    (setup: create) delete           -> no longer listed

Every run tears down its own item and then checks nothing is left behind.
A tool error, a wrong end state, or a leftover item fails the run.
Task score = 10 * passed / runs. Suite score = mean of task scores.

  uv run --with "mcp>=1.10,<2" python todo_score.py --out ../traces/todos.json --traces ../traces/todos [--only todoist,notion]
"""

import argparse
import asyncio
import json
import math
import os
import secrets
import statistics
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from todo_env import load_env, load_state, redact, result_text, session_for

RUNS = 3
TASKS = ["create", "list", "update", "complete", "delete", "messy-title", "due-date", "busy-list", "bulk-add",
         "nlp-date", "recurring", "search-by-description", "subtasks", "error-semantics", "busy-list-newest"]
CALL_TIMEOUT_S = 60


class ToolFailed(Exception):
    pass


def is_error_body(text: str) -> bool:
    """An explicit error object in the response body, e.g. Notion's {"object":"error","status":400,...}."""
    try:
        j = parse_json(text)
    except Exception:  # noqa: BLE001
        return False
    return isinstance(j, dict) and (j.get("object") == "error"
                                    or (isinstance(j.get("status"), int) and j["status"] >= 400))


def parse_json(text: str):
    starts = [p for p in (text.find("{"), text.find("[")) if p >= 0]
    if not starts:
        raise ToolFailed(f"no JSON in response: {text[:160]}")
    return json.JSONDecoder().raw_decode(text[min(starts):])[0]


class Adapter:
    """Maps the five abstract operations onto one server's own MCP tools."""

    def __init__(self, session, state):
        self.s, self.st = session, state

    async def call(self, tool: str, args: dict):
        r = await asyncio.wait_for(self.s.call_tool(tool, args), timeout=CALL_TIMEOUT_S)
        text = result_text(r)
        if r.isError or is_error_body(text):  # some servers return errors as a normal-looking body
            raise ToolFailed(f"{tool} error: {text[:200]}")
        return text

    async def raw(self, tool: str, args: dict):
        r = await asyncio.wait_for(self.s.call_tool(tool, args), timeout=CALL_TIMEOUT_S)
        return bool(r.isError), result_text(r)

    # subclasses implement: create(title)->id, items()->[(id, title)], rename(id, title),
    # complete(id), is_complete(id)->bool, delete(id), leftovers(id)->bool (True if still present)


class Todoist(Adapter):
    async def create(self, title):
        j = parse_json(await self.call("add-tasks", {"tasks": [{"content": title, "projectId": self.st["projectId"]}]}))
        return j["tasks"][0]["id"]

    async def items(self, project=None):
        j = parse_json(await self.call("find-tasks", {"projectId": project or self.st["projectId"]}))  # server default limit
        return [(t["id"], t["content"]) for t in j["tasks"]]

    async def rename(self, i, title):
        await self.call("update-tasks", {"tasks": [{"id": i, "content": title}]})

    async def complete(self, i):
        await self.call("complete-tasks", {"ids": [i]})

    async def is_complete(self, i):
        return bool(parse_json(await self.call("fetch-object", {"type": "task", "id": i}))["object"].get("checked"))

    async def delete(self, i):
        await self.call("delete-object", {"type": "task", "id": i})

    async def leftovers(self, i):
        r = await self.s.call_tool("fetch-object", {"type": "task", "id": i})
        return not r.isError  # deleted tasks 404

    async def create_due(self, title, due):  # Todoist's due input is natural language
        j = parse_json(await self.call("add-tasks", {"tasks": [
            {"content": title, "projectId": self.st["projectId"], "dueString": "tomorrow at 5pm"}]}))
        return j["tasks"][0]["id"]

    async def read_due(self, i):
        obj = parse_json(await self.call("fetch-object", {"type": "task", "id": i}))["object"]
        return (obj.get("due") or {}).get("date") or obj.get("dueDate")

    async def bulk_create(self, titles):
        j = parse_json(await self.call("add-tasks", {"tasks": [{"content": t, "projectId": self.st["projectId"]} for t in titles]}))
        return [t["id"] for t in j["tasks"]]

    async def busy_items(self):
        return await self.items(self.st["busyProjectId"])

    async def create_nl_due(self, title, phrase):
        j = parse_json(await self.call("add-tasks", {"tasks": [{"content": title, "projectId": self.st["projectId"], "dueString": phrase}]}))
        return j["tasks"][0]["id"]

    async def create_recurring(self, title, phrase):
        return await self.create_nl_due(title, phrase)

    async def read_recurring(self, i):
        obj = parse_json(await self.call("fetch-object", {"type": "task", "id": i}))["object"]
        return bool(obj.get("recurring") or (obj.get("due") or {}).get("isRecurring"))

    async def search(self, keyword):
        j = parse_json(await self.call("find-tasks", {"searchText": keyword}))
        return [(t["id"], t["content"]) for t in j["tasks"]]

    async def create_with_subtasks(self, parent, children):
        pid = await self.create(parent)
        j = parse_json(await self.call("add-tasks", {"tasks": [
            {"content": c, "projectId": self.st["projectId"], "parentId": pid} for c in children]}))
        return pid, [t["id"] for t in j["tasks"]]

    async def read_children(self, pid):
        j = parse_json(await self.call("find-tasks", {"parentId": pid}))
        return [t["content"] for t in j["tasks"]]

    async def complete_stale(self, i):
        return await self.raw("complete-tasks", {"ids": [i]})

    async def create_in_busy(self, title):
        j = parse_json(await self.call("add-tasks", {"tasks": [{"content": title, "projectId": self.st["busyProjectId"]}]}))
        return j["tasks"][0]["id"]


class Trello(Adapter):
    async def create(self, title):
        j = parse_json(await self.call("add_card_to_list", {"boardId": self.st["boardId"], "listId": self.st["listId"], "name": title}))
        return j["id"]

    async def _list(self, list_id):
        j = parse_json(await self.call("get_cards_by_list_id", {"boardId": self.st["boardId"], "listId": list_id}))
        return [(c["id"], c["name"]) for c in j]

    async def items(self):
        return await self._list(self.st["listId"])

    async def rename(self, i, title):
        await self.call("update_card_details", {"boardId": self.st["boardId"], "cardId": i, "name": title})

    async def complete(self, i):  # Trello idiom: move the card to the Done list
        await self.call("move_card", {"boardId": self.st["boardId"], "cardId": i, "listId": self.st["doneListId"]})

    async def is_complete(self, i):
        return any(c == i for c, _ in await self._list(self.st["doneListId"]))

    async def delete(self, i):  # no delete tool exists; archive is the closest
        await self.call("archive_card", {"boardId": self.st["boardId"], "cardId": i})

    async def leftovers(self, i):
        ids = []
        for lid in (self.st["listId"], self.st["doneListId"], self.st["busyListId"]):
            ids += [c for c, _ in await self._list(lid)]
        return i in ids

    async def create_due(self, title, due):
        j = parse_json(await self.call("add_card_to_list", {"boardId": self.st["boardId"], "listId": self.st["listId"],
                                                            "name": title, "dueDate": due.isoformat()}))
        return j["id"]

    async def read_due(self, i):
        return parse_json(await self.call("get_card", {"cardId": i})).get("due")

    async def bulk_create(self, titles):
        j = parse_json(await self.call("add_cards_to_list", {"listId": self.st["listId"], "cards": [{"name": t} for t in titles]}))
        cards = (j.get("created") or j.get("cards") or []) if isinstance(j, dict) else j
        return [c["id"] for c in cards]

    async def busy_items(self):
        return await self._list(self.st["busyListId"])

    async def create_nl_due(self, title, phrase):  # dueDate is documented as ISO 8601
        j = parse_json(await self.call("add_card_to_list", {"boardId": self.st["boardId"], "listId": self.st["listId"],
                                                            "name": title, "dueDate": phrase}))
        return j["id"]

    async def create_recurring(self, title, phrase):  # no recurrence field: a one-time card
        return await self.create(title)

    async def read_recurring(self, i):
        return False

    async def search(self, keyword):
        j = parse_json(await self.call("get_cards_by_list_id", {"boardId": self.st["boardId"], "listId": self.st["listId"],
                                                                "nameFilter": keyword}))
        return [(c["id"], c["name"]) for c in j]

    async def create_with_subtasks(self, parent, children):  # Trello's sub-items are checklist items
        pid = await self.create(parent)
        await self.call("create_checklist", {"name": "Subtasks", "cardId": pid})
        for c in children:
            await self.call("add_checklist_item", {"text": c, "checkListName": "Subtasks", "cardId": pid})
        return pid, []

    async def read_children(self, pid):
        j = parse_json(await self.call("get_checklist_items", {"name": "Subtasks", "cardId": pid}))
        items = j.get("items", j) if isinstance(j, dict) else j
        return [x.get("name") or x.get("text") for x in items]

    async def complete_stale(self, i):
        return await self.raw("move_card", {"boardId": self.st["boardId"], "cardId": i, "listId": self.st["doneListId"]})

    async def create_in_busy(self, title):
        j = parse_json(await self.call("add_card_to_list", {"boardId": self.st["boardId"], "listId": self.st["busyListId"], "name": title}))
        return j["id"]


class Notion(Adapter):
    def _title(self, page):
        return "".join(x["plain_text"] for x in page["properties"][self.st["titleProp"]]["title"])

    async def create(self, title):
        j = parse_json(await self.call("API-post-page", {
            "parent": {"database_id": self.st["databaseId"]},
            "properties": {self.st["titleProp"]: {"title": [{"text": {"content": title}}]}}}))
        return j["id"]

    async def items(self):
        j = parse_json(await self.call("API-query-data-source", {"data_source_id": self.st["dataSourceId"]}))  # server default page size
        return [(p["id"], self._title(p)) for p in j["results"]]

    async def rename(self, i, title):
        await self.call("API-patch-page", {"page_id": i, "properties": {self.st["titleProp"]: {"title": [{"text": {"content": title}}]}}})

    async def complete(self, i):
        t = self.st["statusType"]
        await self.call("API-patch-page", {"page_id": i, "properties": {self.st["statusProp"]: {t: {"name": self.st["done"]}}}})

    async def is_complete(self, i):
        p = parse_json(await self.call("API-retrieve-a-page", {"page_id": i}))
        v = p["properties"][self.st["statusProp"]].get(self.st["statusType"]) or {}
        return v.get("name") == self.st["done"]

    async def delete(self, i):  # Notion's delete is "move to trash"
        await self.call("API-patch-page", {"page_id": i, "in_trash": True})

    async def leftovers(self, i):
        return i in [p for p, _ in await self.items()]

    async def create_due(self, title, due):
        j = parse_json(await self.call("API-post-page", {
            "parent": {"database_id": self.st["databaseId"]},
            "properties": {self.st["titleProp"]: {"title": [{"text": {"content": title}}]},
                           self.st["dueProp"]: {"date": {"start": due.isoformat()}}}}))
        return j["id"]

    async def read_due(self, i):
        p = parse_json(await self.call("API-retrieve-a-page", {"page_id": i}))
        return ((p["properties"][self.st["dueProp"]].get("date")) or {}).get("start")

    async def bulk_create(self, titles):  # no bulk tool: one call per item
        return [await self.create(t) for t in titles]

    async def busy_items(self):
        return await self.items()

    async def create_nl_due(self, title, phrase):  # date.start is documented as ISO 8601
        j = parse_json(await self.call("API-post-page", {
            "parent": {"database_id": self.st["databaseId"]},
            "properties": {self.st["titleProp"]: {"title": [{"text": {"content": title}}]},
                           self.st["dueProp"]: {"date": {"start": phrase}}}}))
        return j["id"]

    async def create_recurring(self, title, phrase):  # no recurrence property: a one-time page
        return await self.create(title)

    async def read_recurring(self, i):
        return False

    async def search(self, keyword):
        j = parse_json(await self.call("API-post-search", {"query": keyword}))
        out = []
        for p in j.get("results", []):
            props = p.get("properties", {})
            t = props.get(self.st["titleProp"], {}).get("title") if self.st["titleProp"] in props else None
            out.append((p["id"], "".join(x["plain_text"] for x in t) if t else ""))
        return out

    async def create_with_subtasks(self, parent, children):  # the database's built-in sub-item relation
        pid = await self.create(parent)
        kids = []
        for c in children:
            j = parse_json(await self.call("API-post-page", {
                "parent": {"database_id": self.st["databaseId"]},
                "properties": {self.st["titleProp"]: {"title": [{"text": {"content": c}}]},
                               "Parent item": {"relation": [{"id": pid}]}}}))
            kids.append(j["id"])
        return pid, kids

    async def read_children(self, pid):
        p = parse_json(await self.call("API-retrieve-a-page", {"page_id": pid}))
        out = []
        for rel in p["properties"]["Sub-item"]["relation"]:
            child = parse_json(await self.call("API-retrieve-a-page", {"page_id": rel["id"]}))
            out.append(self._title(child))
        return out

    async def complete_stale(self, i):
        return await self.raw("API-patch-page", {"page_id": i, "properties": {
            self.st["statusProp"]: {self.st["statusType"]: {"name": self.st["done"]}}}})

    async def create_in_busy(self, title):
        return await self.create(title)


class Airtable(Adapter):
    async def create(self, title):
        j = parse_json(await self.call("create_record", {"baseId": self.st["baseId"], "tableId": self.st["tableId"],
                                                         "fields": {"Title": title, "Status": "Todo"}}))
        return j["id"]

    async def items(self):
        j = parse_json(await self.call("list_records", {"baseId": self.st["baseId"], "tableId": self.st["tableId"]}))  # server default maxRecords
        recs = j.get("records", j) if isinstance(j, dict) else j
        return [(r["id"], r["fields"].get("Title", "")) for r in recs]

    async def rename(self, i, title):
        await self.call("update_records", {"baseId": self.st["baseId"], "tableId": self.st["tableId"],
                                           "records": [{"id": i, "fields": {"Title": title}}]})

    async def complete(self, i):
        await self.call("update_records", {"baseId": self.st["baseId"], "tableId": self.st["tableId"],
                                           "records": [{"id": i, "fields": {"Status": "Done"}}]})

    async def is_complete(self, i):
        j = parse_json(await self.call("get_record", {"baseId": self.st["baseId"], "tableId": self.st["tableId"], "recordId": i}))
        return j["fields"].get("Status") == "Done"

    async def delete(self, i):
        await self.call("delete_records", {"baseId": self.st["baseId"], "tableId": self.st["tableId"], "recordIds": [i]})

    async def leftovers(self, i):
        return i in [r for r, _ in await self.items()]

    async def create_due(self, title, due):
        j = parse_json(await self.call("create_record", {"baseId": self.st["baseId"], "tableId": self.st["tableId"],
                                                         "fields": {"Title": title, "Status": "Todo", "Due": due.isoformat()}}))
        return j["id"]

    async def read_due(self, i):
        j = parse_json(await self.call("get_record", {"baseId": self.st["baseId"], "tableId": self.st["tableId"], "recordId": i}))
        return j["fields"].get("Due")

    async def bulk_create(self, titles):  # no bulk tool: one call per item
        return [await self.create(t) for t in titles]

    async def busy_items(self):
        return await self.items()

    async def create_nl_due(self, title, phrase):  # Due is a dateTime field
        j = parse_json(await self.call("create_record", {"baseId": self.st["baseId"], "tableId": self.st["tableId"],
                                                         "fields": {"Title": title, "Status": "Todo", "Due": phrase}}))
        return j["id"]

    async def create_recurring(self, title, phrase):  # no recurrence field type: a one-time record
        return await self.create(title)

    async def read_recurring(self, i):
        return False

    async def search(self, keyword):
        j = parse_json(await self.call("search_records", {"baseId": self.st["baseId"], "tableId": self.st["tableId"], "searchTerm": keyword}))
        recs = j.get("records", j) if isinstance(j, dict) else j
        return [(r["id"], r["fields"].get("Title", "")) for r in recs]

    async def create_with_subtasks(self, parent, children):  # self-linking "Parent" field (added in setup)
        pid = await self.create(parent)
        kids = []
        for c in children:
            j = parse_json(await self.call("create_record", {"baseId": self.st["baseId"], "tableId": self.st["tableId"],
                                                             "fields": {"Title": c, "Status": "Todo", "Parent": [pid]}}))
            kids.append(j["id"])
        return pid, kids

    async def read_children(self, pid):
        j = parse_json(await self.call("get_record", {"baseId": self.st["baseId"], "tableId": self.st["tableId"], "recordId": pid}))
        out = []
        for cid in j["fields"].get("From field: Parent", []):
            c = parse_json(await self.call("get_record", {"baseId": self.st["baseId"], "tableId": self.st["tableId"], "recordId": cid}))
            out.append(c["fields"].get("Title", ""))
        return out

    async def complete_stale(self, i):
        return await self.raw("update_records", {"baseId": self.st["baseId"], "tableId": self.st["tableId"],
                                                 "records": [{"id": i, "fields": {"Status": "Done"}}]})

    async def create_in_busy(self, title):
        return await self.create(title)


ADAPTERS = {"todoist": Todoist, "trello": Trello, "notion": Notion, "airtable": Airtable}


BUSY_TARGET = "Renew passport [busy-target]"  # oldest item in each server's 30-item busy fixture
TZ = ZoneInfo("America/Los_Angeles")          # the test account's timezone


def due_matches(raw, expected: datetime) -> bool:
    """True if the server's stored due value denotes `expected` (tomorrow 17:00 local)."""
    if not raw or "T" not in str(raw):
        return False  # missing, or date-only (time lost)
    v = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    v = v.replace(tzinfo=TZ) if v.tzinfo is None else v.astimezone(TZ)  # Todoist stores floating local time
    return (v.date(), v.hour, v.minute) == (expected.date(), expected.hour, expected.minute)


def clear_error(is_error: bool, text: str) -> bool:
    """error-semantics rule (fixed before the scored run): PASS if the tool reports an error (isError),
    the body is an explicit error object (object == "error" or status >= 400), or the response explicitly
    reports this item as failed (non-empty failures list, failureCount > 0, or successCount == 0).
    Anything success-shaped is a FAIL: the agent would believe it acted."""
    if is_error or is_error_body(text):
        return True
    try:
        j = parse_json(text)
    except Exception:  # noqa: BLE001 - prose with no structure is ambiguous
        return False
    if isinstance(j, dict):
        if j.get("failures") or (j.get("failureCount") or 0) > 0 or j.get("successCount") == 0:
            return True
    return False


async def run_task(server_id: str, task: str, run: int, state: dict) -> dict:
    tag = f"[run-{run}-{secrets.token_hex(2)}]"
    title, new_title = f"Buy milk {tag}", f"Buy oat milk {tag}"
    steps, created = [], []
    started = time.time()
    try:
        async with session_for(server_id) as s:
            a = ADAPTERS[server_id](s, state[server_id])
            if task == "busy-list":  # persistent fixture: nothing to create or tear down
                ok = BUSY_TARGET in [t for _, t in await a.busy_items()]
                check = "oldest of 30 items found with default page size"
                steps.append(f"check '{check}': {'ok' if ok else 'FAILED'}")
                return {"ok": ok, "latency_s": round(time.time() - started, 1), "steps": steps,
                        "error": None if ok else f"failed check: {check}"}
            if task in NEW_TASKS:
                return await run_new_task(a, task, tag, steps, started)
            if task == "messy-title":
                title = f'Buy milk \U0001F95B & café, "oat" <2%> {tag}'
            if task == "due-date":
                expected = (datetime.now(TZ) + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
                created = [await a.create_due(title, expected)]
            elif task == "bulk-add":
                titles = [f"Bulk item {k} {tag}" for k in range(1, 6)]
                created = await a.bulk_create(titles)
            else:
                created = [await a.create(title)]
            steps.append(f"create -> {created}")
            item = created[0]
            if task in ("create", "messy-title"):
                ok = any(i == item and t == title for i, t in await a.items())
                check = "listed with exact title"
            elif task == "list":
                ok = title in [t for _, t in await a.items()]
                check = "appears in list"
            elif task == "update":
                await a.rename(item, new_title)
                titles_now = [t for _, t in await a.items()]
                ok = new_title in titles_now and title not in titles_now
                check = "new title listed, old title gone"
            elif task == "complete":
                await a.complete(item)
                ok = await a.is_complete(item)
                check = "server reports complete"
            elif task == "due-date":
                raw = await a.read_due(item)
                ok = due_matches(raw, expected)
                check = f"due reads back as {expected.isoformat()} (got {raw})"
            elif task == "bulk-add":
                listed = [t for _, t in await a.items()]
                ok = len(created) == 5 and all(t in listed for t in titles)
                check = "all 5 listed"
            else:  # delete
                await a.delete(item)
                ok = item not in [i for i, _ in await a.items()]
                check = "no longer listed"
                created = [] if ok else created
            steps.append(f"check '{check}': {'ok' if ok else 'FAILED'}")
            # teardown + leftover check
            leftover = False
            for i in created:
                await a.delete(i)
                leftover = leftover or await a.leftovers(i)
            if created:
                steps.append(f"teardown: {'LEFTOVER' if leftover else 'clean'}")
            error = None if ok and not leftover else ("leftover item after teardown" if ok else f"failed check: {check}")
            return {"ok": ok and not leftover, "latency_s": round(time.time() - started, 1), "steps": steps, "error": error}
    except Exception as e:  # noqa: BLE001 - a crash is a failed run, recorded in the trace
        while hasattr(e, "exceptions") and len(e.exceptions) == 1:
            e = e.exceptions[0]
        return {"ok": False, "latency_s": round(time.time() - started, 1), "steps": steps,
                "error": f"{type(e).__name__}: {str(e)[:240]}"}


NEW_TASKS = {"nlp-date", "recurring", "search-by-description", "subtasks", "error-semantics", "busy-list-newest"}


async def run_new_task(a, task, tag, steps, started):
    created = []  # every id here is torn down and leftover-checked at the end
    ok, check = False, ""
    if task == "nlp-date":
        expected = (datetime.now(TZ) + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
        created = [await a.create_nl_due(f"Call dentist {tag}", "tomorrow at 5pm")]
        raw = await a.read_due(created[0])
        ok = due_matches(raw, expected)
        check = f"'tomorrow at 5pm' stored as {expected.isoformat()} (got {raw})"
    elif task == "recurring":
        created = [await a.create_recurring(f"Water plants {tag}", "every Monday")]
        ok = await a.read_recurring(created[0])
        check = "stored as a recurring rule"
    elif task == "search-by-description":
        target = f"Renew passport before December {tag}"
        titles = [target] + [f"Errand {k} {tag}" for k in range(1, 10)]
        created = await a.bulk_create(titles)
        tid = created[0]
        hits = await a.search("passport")
        ok = tid in [i for i, _ in hits] or target in [t for _, t in hits]
        check = f"'passport' search returns the target ({len(hits)} results)"
    elif task == "subtasks":
        kids = [f"Book flights {tag}", f"Book hotel {tag}"]
        pid, kid_ids = await a.create_with_subtasks(f"Plan trip {tag}", kids)
        created = kid_ids + [pid]  # children first so teardown never orphans them
        got = await a.read_children(pid)
        ok = sorted(got) == sorted(kids)
        check = f"parent has exactly the 2 children (got {got})"
    elif task == "error-semantics":
        i = await a.create(f"Stale item {tag}")
        await a.delete(i)
        is_err, text = await a.complete_stale(i)
        ok = clear_error(is_err, text)
        check = f"completing a deleted item returns a clear error (isError={is_err}, response={text[:120]!r})"
    else:  # busy-list-newest
        created = [await a.create_in_busy(f"Just added {tag}")]
        ok = created[0] in [i for i, _ in await a.busy_items()]
        check = "newest item is in the default read"
    steps.append(f"check '{check}': {'ok' if ok else 'FAILED'}")
    leftover = False
    for i in created:
        try:
            await a.delete(i)
        except ToolFailed:
            pass  # e.g. a subtask already removed with its parent; the leftover check decides
        leftover = leftover or await a.leftovers(i)
    if created:
        steps.append(f"teardown: {'LEFTOVER' if leftover else 'clean'}")
    error = None if ok and not leftover else ("leftover item after teardown" if ok else f"failed check: {check}")
    return {"ok": ok and not leftover, "latency_s": round(time.time() - started, 1), "steps": steps, "error": error}


def ci95(xs):
    if len(xs) < 2:
        return (xs[0], xs[0]) if xs else (0.0, 0.0)
    m, sd = statistics.mean(xs), statistics.stdev(xs)
    t = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57}.get(len(xs) - 1, 1.96)
    return max(0.0, m - t * sd / math.sqrt(len(xs))), min(10.0, m + t * sd / math.sqrt(len(xs)))


async def main(args):
    env, state = load_env(), load_state()
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "todo_servers.json")) as f:
        servers = json.load(f)["servers"]
    if args.only:
        servers = [s for s in servers if s["id"] in args.only.split(",")]
    os.makedirs(args.traces, exist_ok=True)
    out = []
    for srv in servers:
        sid = srv["id"]
        print(f"\n=== {sid} ===", flush=True)
        per_task = {}
        for task in TASKS:
            runs = []
            for i in range(1, RUNS + 1):
                r = await run_task(sid, task, i, state)
                r["error"] = redact(r["error"], env) if r["error"] else None
                runs.append(r)
                print(f"  [{task}] run {i}: {'PASS' if r['ok'] else 'FAIL ' + (r['error'] or '')} ({r['latency_s']}s)", flush=True)
            per_task[task] = runs
        tasks_out = {}
        for task, runs in per_task.items():
            passed = sum(r["ok"] for r in runs)
            exact = 10 * passed / len(runs)
            ok_lat = [r["latency_s"] for r in runs if r["ok"]]
            tasks_out[task] = {
                "score": round(exact, 1), "score_exact": exact,
                "runs_completed": f"{passed}/{len(runs)}",
                "wer": None, "keyphrase_recall": 0, "missed_phrases": [],
                "latency_s": round(statistics.mean(ok_lat), 1) if ok_lat else None,
                "sample_transcript": "",
                "errors": sorted({r["error"] for r in runs if r["error"]}),
            }
        overall = [statistics.mean(10.0 * per_task[t][i]["ok"] for t in TASKS) for i in range(RUNS)]
        lo, hi = ci95(overall)
        all_runs = [r for rs in per_task.values() for r in rs]
        passed_lat = [r["latency_s"] for r in all_runs if r["ok"]]
        entry = {
            "id": sid, "name": srv["name"], "repo": srv.get("repo", ""), "backend": srv.get("backend", ""),
            "score": round(statistics.mean(overall), 1), "score_exact": statistics.mean(overall),
            "ci95": [round(lo, 1), round(hi, 1)],
            "score_range": [round(min(overall), 1), round(max(overall), 1)],
            "runs_completed": f"{sum(r['ok'] for r in all_runs)}/{len(all_runs)}",
            "wer": None, "keyphrase_recall": 0, "missed_phrases": [],
            "latency_s": round(statistics.mean(passed_lat), 1) if passed_lat else None,
            "sample_transcript": "",
            "errors": sorted({e for t in tasks_out.values() for e in t["errors"]}),
            "failed_tasks": [t for t, v in tasks_out.items() if v["runs_completed"].startswith("0/")],
            "tasks": tasks_out,
        }
        out.append(entry)
        with open(os.path.join(args.traces, f"{sid}.json"), "w") as f:
            json.dump({"server": {k: v for k, v in srv.items() if k != "env"}, "runs": per_task, "summary": entry}, f, indent=2)
    out.sort(key=lambda s: s["score"], reverse=True)
    report = {
        "task_category": "todos",
        "suite": "todos-v1",
        "scoring": "pass_fail",
        "fixture": "live test project/list/table per server",
        "fixture_desc": "15 tasks: 5 basics (create, list, update, complete, delete), 4 edge cases (messy-title, due-date, busy-list, bulk-add), 6 agent-realism tasks (nlp-date, recurring, search-by-description, subtasks, error-semantics, busy-list-newest); each run creates and tears down its own items; busy-list tasks use a persistent 30-item fixture",
        "tasks": [
            {"id": "create", "desc": "add 'Buy milk [run-N]', verify it is listed with the exact title", "weight": 1},
            {"id": "list", "desc": "list the test project, verify the created item appears", "weight": 1},
            {"id": "update", "desc": "rename to 'Buy oat milk [run-N]', verify the new title (old title gone)", "weight": 1},
            {"id": "complete", "desc": "mark it done, verify the server reports it complete", "weight": 1},
            {"id": "delete", "desc": "delete it, verify it is no longer listed", "weight": 1},
            {"id": "messy-title", "desc": "add a title with emoji, accent, ampersand, comma, quotes and angle brackets; verify it reads back exactly", "weight": 1},
            {"id": "due-date", "desc": "add an item due tomorrow at 5pm (account timezone); verify the stored due date and time", "weight": 1},
            {"id": "busy-list", "desc": "find the oldest of 30 items using the server's default list page size", "weight": 1},
            {"id": "bulk-add", "desc": "add 5 items at once (bulk tool where the server has one); verify all 5 are listed", "weight": 1},
            {"id": "nlp-date", "desc": "add 'Call dentist' with the date given as the phrase 'tomorrow at 5pm'; verify it is stored as tomorrow 17:00 (account timezone)", "weight": 1},
            {"id": "recurring", "desc": "add 'Water plants every Monday'; PASS only if stored as a recurring rule (one-time item or error = FAIL)", "weight": 1},
            {"id": "search-by-description", "desc": "seed 10 items including 'Renew passport before December'; search 'passport' with the server's search tool; verify the target is returned", "weight": 1},
            {"id": "subtasks", "desc": "add 'Plan trip' with subtasks 'Book flights' and 'Book hotel'; verify the parent has exactly those 2 children", "weight": 1},
            {"id": "error-semantics", "desc": "complete an item that was just deleted; PASS = clear error, FAIL = silent success or ambiguous response", "weight": 1},
            {"id": "busy-list-newest", "desc": "add an item to a 30-item list; verify the newest item is in the default read", "weight": 1},
        ],
        "method": (f"fixed harness invocation per server, server defaults (including default list page sizes), {len(TASKS)} tasks x {RUNS} runs; every check reads state "
                   "back through the same MCP server; binary pass/fail per run (tool error, wrong end state, or leftover "
                   "item after teardown = fail); task score = 10 x passed/runs; suite score = mean of task scores"),
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "servers": out,
    }
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)
    print("\n===== SCOREBOARD (todos-v1) =====")
    print(f"  {'suite':>5}  {'server':<9} " + " ".join(f"{t:>9}" for t in TASKS))
    for s in out:
        print(f"  {s['score']:>5}  {s['id']:<9} " + " ".join(f"{s['tasks'][t]['score']:>9}" for t in TASKS))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--traces", required=True)
    ap.add_argument("--only", default="")
    asyncio.run(main(ap.parse_args()))
