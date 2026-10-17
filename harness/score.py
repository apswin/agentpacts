#!/usr/bin/env python3
"""
AgentPacts scoring harness — transcription bake-off.

Spawns each candidate MCP server over stdio, calls its transcribe tool with
the SAME audio fixture, and scores the transcript against ground truth.

The harness IS the fixed agent: identical invocation, no LLM in the loop,
no prompt variance. For single-tool transcription tasks the agent loop is
trivially fixed by construction; the multi-step agentic loop matters for
multi-tool tasks, not here. Say exactly that if a judge asks.

Every server runs on its out-of-the-box defaults (we pass only the file).

Scoring (per run):
  accuracy   = 1 - WER, clipped to [0, 1]  (jiwer, after OpenAI's Whisper
               EnglishTextNormalizer, so "47,300" == "forty-seven thousand
               three hundred" and punctuation/casing never count)
  keyphrase  = fraction of key phrases (proper nouns, numbers, jargon)
               present in the normalized transcript
  score      = 10 * (0.6 * accuracy + 0.4 * keyphrase)
A failed run (crash, timeout, empty output) scores 0 — reliability counts.
Server score = mean over runs, with a 95% CI.

Usage (or just ../run_bakeoff.sh):
  uv run --with "mcp>=1.10,<2" --with jiwer --with whisper-normalizer \
      python score.py --config servers.json --suite suite.json \
      --out ../traces/scores.json --traces ../traces
"""

import argparse
import asyncio
import json
import math
import os
import re
import statistics
import time
from datetime import datetime, timezone

import jiwer
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from whisper_normalizer.english import EnglishTextNormalizer

CALL_TIMEOUT_S = 600
NORMALIZE = EnglishTextNormalizer()


def accuracy(truth: str, transcript: str) -> tuple[float, float]:
    """Returns (accuracy, wer) on normalized text."""
    t, h = NORMALIZE(truth), NORMALIZE(transcript)
    if not t.strip():
        return 0.0, 1.0
    if not h.strip():
        return 0.0, 1.0
    wer = jiwer.wer(t, h)
    return max(0.0, 1.0 - wer), wer


def simple(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s$%]", " ", text.lower()).split())


def keyphrase_hits(phrases: list[list[str]], transcript: str) -> tuple[float, list[str]]:
    """Each phrase is a list of accepted spellings; returns (recall, missed)."""
    if not phrases:
        return 1.0, []
    # Two views of the text: the Whisper normalizer (numbers -> digits) and a
    # plain lowercase/no-punctuation pass, because the normalizer can merge
    # across sentence boundaries ("November 9. Second" -> "november 92nd").
    views = [f" {NORMALIZE(transcript)} ", f" {simple(transcript)} "]
    missed = []
    for alts in phrases:
        if not any(f" {f(a)} " in v for a in alts for f in (NORMALIZE, simple) for v in views):
            missed.append(alts[0])
    return (len(phrases) - len(missed)) / len(phrases), missed


def ci95(xs: list[float]) -> tuple[float, float]:
    if len(xs) < 2:
        return (xs[0], xs[0]) if xs else (0.0, 0.0)
    m, sd = statistics.mean(xs), statistics.stdev(xs)
    # t critical values for small n (df = n-1), else normal approx
    t = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57}.get(len(xs) - 1, 1.96)
    half = t * sd / math.sqrt(len(xs))
    return max(0.0, m - half), min(10.0, m + half)


async def run_server(cfg: dict, fixture: str, timeout: int) -> dict:
    """Spawn one server, call its transcribe tool, return raw result + latency."""
    tool_args = {k: (v.replace("{fixture}", fixture)
                      .replace("{fixture_name}", os.path.basename(fixture))
                     if isinstance(v, str) else v)
                 for k, v in cfg["tool_args"].items()}
    # Merge environments defensively: server env wins, OS env preserved.
    env = {**os.environ, **cfg.get("env", {})}
    params = StdioServerParameters(
        command=cfg["command"], args=cfg["args"], env=env, cwd=cfg.get("cwd"))
    started = time.time()
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=60)
                result = await asyncio.wait_for(
                    session.call_tool(cfg["tool"], tool_args),
                    timeout=timeout)
        latency = time.time() - started
        texts = [b.text for b in result.content
                 if getattr(b, "type", "") == "text"]
        raw = "\n".join(texts).strip()
        transcript = extract_text(raw)
        # Some servers report failures as ordinary text instead of isError.
        if result.isError or not transcript or TOOL_ERROR.match(transcript):
            return {"ok": False, "error": f"tool error: {raw[:300]}",
                    "latency_s": round(latency, 1), "transcript": "",
                    "raw": raw, "tool_args": tool_args}
        return {"ok": True, "transcript": transcript, "raw": raw,
                "latency_s": round(latency, 1), "tool_args": tool_args}
    except Exception as e:  # noqa: BLE001 - trace must survive failures
        while hasattr(e, "exceptions") and len(e.exceptions) == 1:
            e = e.exceptions[0]  # unwrap anyio TaskGroup nesting to the real cause
        msg = f"{type(e).__name__}: {e}"
        return {"ok": False, "error": msg, "tool_args": tool_args,
                "latency_s": round(time.time() - started, 1),
                "transcript": ""}


TOOL_ERROR = re.compile(r"^\s*(\w+\s+)?(error|failed|exception)\s*:", re.I)
TIMESTAMP = re.compile(r"\[\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}\]")


def extract_text(raw: str) -> str:
    """Some servers return JSON ({"text": ...}); others return prose.
    Segment timestamps are stripped so output format never affects score."""
    text = raw
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict):
        for k in ("text", "transcript", "transcription", "content"):
            if isinstance(obj.get(k), str):
                text = obj[k]
                break
    return " ".join(TIMESTAMP.sub(" ", text).split())


def load_phrases(path: str) -> list[list[str]]:
    if not path:
        return []
    with open(path) as f:
        return [[a.strip() for a in line.split("|") if a.strip()]
                for line in f if line.strip()]


def summarize(runs: list[dict], phrases: list[list[str]]) -> dict:
    ok = [r for r in runs if r.get("ok")]
    scores = [r["score"] for r in runs]
    best = max(ok, key=lambda r: r["score"]) if ok else None
    return {
        "score": round(statistics.mean(scores), 1) if scores else 0.0,
        "score_exact": statistics.mean(scores) if scores else 0.0,  # unrounded; gates use this
        "runs_completed": f"{len(ok)}/{len(runs)}",
        "wer": round(statistics.mean(r["wer"] for r in ok), 3) if ok else None,
        "keyphrase_recall": round(statistics.mean(r["keyphrase_recall"] for r in ok), 3) if ok else 0,
        "missed_phrases": best["missed_phrases"] if best else [p[0] for p in phrases],
        "latency_s": round(statistics.mean(r["latency_s"] for r in ok), 1) if ok else None,
        "sample_transcript": best["transcript"][:600] if best else "",
        "errors": sorted({r["error"][:300] for r in runs if not r.get("ok")}),
    }


async def score_task(s: dict, task: dict, n_runs: int) -> list[dict]:
    with open(task["truth"]) as f:
        truth = f.read().strip()
    phrases = load_phrases(task.get("key_phrases", ""))
    runs = []
    for i in range(n_runs):
        r = await run_server(s, task["fixture"], CALL_TIMEOUT_S)
        if not r["ok"]:
            print(f"  [{task['id']}] run {i + 1}: FAILED {r['error'][:160]}", flush=True)
            runs.append({**r, "score": 0.0})
            continue
        acc, wer = accuracy(truth, r["transcript"])
        kpr, missed = keyphrase_hits(phrases, r["transcript"])
        score = 10 * (0.6 * acc + 0.4 * kpr)
        print(f"  [{task['id']}] run {i + 1}: score={score:.1f} wer={wer:.2f} "
              f"keyphrase={kpr:.2f} latency={r['latency_s']}s", flush=True)
        runs.append({**r, "wer": round(wer, 3), "accuracy": round(acc, 3),
                     "keyphrase_recall": round(kpr, 3),
                     "missed_phrases": missed, "score": score})
    return runs


async def score_all(args) -> dict:
    with open(args.config) as f:
        cfg = json.load(f)
    with open(args.suite) as f:
        suite = json.load(f)
    base = os.path.dirname(os.path.abspath(args.suite))
    tasks = suite["tasks"]
    for t in tasks:
        for k in ("fixture", "truth", "key_phrases"):
            if t.get(k):
                t[k] = os.path.normpath(os.path.join(base, t[k]))
    n_runs = suite.get("runs", 3)
    total_w = sum(t.get("weight", 1) for t in tasks)
    if args.traces:
        os.makedirs(args.traces, exist_ok=True)

    servers_out = []
    for s in cfg["servers"]:
        if args.only and s["id"] not in args.only.split(","):
            continue
        print(f"\n=== {s['id']} ({s['name']}) ===", flush=True)
        per_task_runs = {t["id"]: await score_task(s, t, n_runs) for t in tasks}
        task_summ = {t["id"]: summarize(per_task_runs[t["id"]], load_phrases(t.get("key_phrases", "")))
                     for t in tasks}
        # Overall score per run index = weighted mean across tasks; CI over runs.
        overall = [sum(t.get("weight", 1) * per_task_runs[t["id"]][i]["score"] for t in tasks) / total_w
                   for i in range(n_runs)]
        lo, hi = ci95(overall)
        all_runs = [r for rs in per_task_runs.values() for r in rs]
        ok = [r for r in all_runs if r.get("ok")]
        failed_tasks = [tid for tid, ts in task_summ.items() if ts["runs_completed"].startswith("0/")]
        entry = {
            "id": s["id"],
            "name": s["name"],
            "repo": s.get("repo", ""),
            "backend": s.get("backend", ""),
            "score": round(statistics.mean(overall), 1),
            "score_exact": statistics.mean(overall),  # unrounded; gates use this
            "ci95": [round(lo, 1), round(hi, 1)],
            "score_range": [round(min(overall), 1), round(max(overall), 1)],
            "runs_completed": f"{len(ok)}/{len(all_runs)}",
            "wer": round(statistics.mean(r["wer"] for r in ok), 3) if ok else None,
            "keyphrase_recall": round(statistics.mean(r["keyphrase_recall"] for r in ok), 3) if ok else 0,
            "missed_phrases": [],
            "latency_s": round(statistics.mean(r["latency_s"] for r in ok), 1) if ok else None,
            "sample_transcript": next((ts["sample_transcript"] for ts in task_summ.values()
                                       if ts["sample_transcript"]), ""),
            "errors": sorted({e for ts in task_summ.values() for e in ts["errors"]}),
            "failed_tasks": failed_tasks,
            "tasks": task_summ,
        }
        servers_out.append(entry)
        if args.traces:
            with open(os.path.join(args.traces, f"{s['id']}.json"), "w") as f:
                json.dump({"server": s, "runs": per_task_runs, "summary": entry}, f, indent=2)

    servers_out.sort(key=lambda s: s["score"], reverse=True)
    return {
        "task_category": suite["task_category"],
        "suite": suite["suite"],
        "fixture": ", ".join(os.path.basename(t["fixture"]) for t in tasks),
        "fixture_desc": f"{len(tasks)} tasks: " + "; ".join(f"{t['id']} ({t['desc']})" for t in tasks),
        "tasks": [{"id": t["id"], "desc": t["desc"], "weight": t.get("weight", 1)} for t in tasks],
        "method": ("fixed harness invocation per server, server defaults, "
                   f"{len(tasks)} tasks x {n_runs} runs; task score = 10 * (0.6 * (1 - WER) + "
                   "0.4 * key-phrase recall) vs ground truth after Whisper "
                   "EnglishTextNormalizer; failed runs score 0; server score = "
                   "weighted mean over tasks, 95% CI over runs"),
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "servers": servers_out,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="servers.json")
    ap.add_argument("--suite", required=True, help="suite.json (tasks, fixtures, truths)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--traces", default="")
    ap.add_argument("--only", default="", help="comma-separated server ids")
    args = ap.parse_args()

    report = asyncio.run(score_all(args))
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)

    print("\n===== SCOREBOARD =====")
    ids = [t["id"] for t in report["tasks"]]
    print(f"  {'score':>5}  {'ci95':<11} {'server':<10} " + "  ".join(f"{i:>14}" for i in ids))
    for s in report["servers"]:
        cells = "  ".join(f"{s['tasks'][i]['score']:>10} ({s['tasks'][i]['runs_completed']})" for i in ids)
        print(f"  {s['score']:>5}  [{s['ci95'][0]}-{s['ci95'][1]}]{'':<2} {s['id']:<10} {cells}")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
