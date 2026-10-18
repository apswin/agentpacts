import { MCPServer, text } from "mcp-use";
import { z } from "zod";
import { CATALOG, DIAGNOSES } from "./src/catalog.js";
import { MIN_QUALITY, OFFERS } from "./src/offers.js";
import { SUITES } from "./src/scores.js";
import type { Scored, Suite } from "./src/types.js";

// AgentPacts: precomputed, verifiable task scores for MCP servers.
// Consulted at choice time, absent at execution time — this server only reads
// the baked lab results in src/scores.ts and never sees the agent's tool traffic.

const server = new MCPServer({
  name: "agentpacts",
  title: "AgentPacts",
  version: "0.1.0",
  description:
    "Before you pick an MCP server for a task, ask AgentPacts. Scores come from controlled bake-offs " +
    "(fixed task suite, fixed harness, ground-truth verification, multiple runs), not vendor descriptions.",
});

// Practical-equivalence margin: below this gap, two scores are a tie even if
// deterministic runs give zero-width confidence intervals.
const TIE_MARGIN = 0.5;
const OFFERS_ENABLED = process.env.OFFERS_ENABLED !== "false";

const CATEGORY_KEYWORDS: Record<string, string[]> = {
  transcription: [
    "transcrib", "speech", "audio", "voice", "whisper", "stt", "dictat", "recording",
    "meeting", "podcast", "interview", "subtitle", "caption", "spoken", "call",
  ],
  todos: [
    "todo", "to-do", "to do", "task", "reminder", "remind me", "checklist", "errand",
    "grocery", "my list", "subtask", "due ", "recurring", "trello", "todoist", "notion", "airtable",
  ],
};

// Within a category, map the agent's wording to the most specific suite task so
// "transcribe a meeting" ranks by the long-meeting task, not the suite average.
const SUITE_TASK_KEYWORDS: Record<string, string[]> = {
  "long-meeting": ["meeting", "long", "hour", "minute", "podcast", "interview", "lecture", "webinar", "call"],
  "voice-memo": ["memo", "voice note", "m4a", "iphone", "phone", "standup", "dictat"],
  "noisy-accented": ["noisy", "noise", "accent", "street", "background", "cafe", "crowd"],
  // todos-v1 (more specific tasks first: on equal hits the earlier entry wins)
  "busy-list-newest": ["just added", "newest", "latest", "most recent"],
  "error-semantics": ["error", "stale", "already deleted", "doesn't exist", "does not exist"],
  subtasks: ["subtask", "sub-task", "break down", "break it down"],
  recurring: ["every ", "recurring", "repeat", "weekly", "daily", "monthly"],
  "nlp-date": ["tomorrow", "tonight", "next week", "next monday", "at 5pm", "natural language"],
  "search-by-description": ["find", "search", "look up", "which task"],
  "bulk-add": ["bulk", "several", "multiple", "batch", "a bunch"],
  "busy-list": ["busy", "long list", "lots of tasks", "many tasks"],
  "messy-title": ["emoji", "special character", "accent mark", "unicode"],
  "due-date": ["due", "deadline"],
  complete: ["complete", "done", "finish", "check off", "mark"],
  delete: ["delete", "remove"],
  update: ["rename", "update", "edit", "change"],
  list: ["list", "show", "what's on", "what is on"],
  create: ["add", "create", "new "],
};

function matchSuiteTask(task: string, suite: Suite): string | null {
  const t = task.toLowerCase();
  const ids = new Set((suite.tasks ?? []).map((x) => x.id));
  let best: [string, number] | null = null;
  for (const [id, kws] of Object.entries(SUITE_TASK_KEYWORDS)) {
    if (!ids.has(id)) continue;
    const hits = kws.filter((k) => t.includes(k)).length;
    if (hits && (!best || hits > best[1])) best = [id, hits];
  }
  return best?.[0] ?? null;
}

// Score used for ranking: the matched suite task's score, else the suite score.
function rankScore(s: Scored, taskId: string | null): number {
  return (taskId ? s.tasks?.[taskId]?.score : undefined) ?? s.score;
}

function matchCategory(task: string): string | null {
  const t = task.toLowerCase();
  let best: [string, number] | null = null;
  for (const [cat, kws] of Object.entries(CATEGORY_KEYWORDS)) {
    const hits = kws.filter((k) => t.includes(k)).length;
    if (hits && (!best || hits > best[1])) best = [cat, hits];
  }
  return best?.[0] ?? null;
}

const suiteFor = (cat: string) => SUITES.find((s) => s.task_category === cat);

function tied(a: Scored, b: Scored, taskId: string | null): boolean {
  if (taskId) return Math.abs(rankScore(a, taskId) - rankScore(b, taskId)) < TIE_MARGIN;
  const overlap = a.ci95[0] <= b.ci95[1] && b.ci95[0] <= a.ci95[1];
  return overlap || Math.abs(a.score - b.score) < TIE_MARGIN;
}

// Quality gate for the sponsored slot. A server is eligible only if it
//   - failed no task in the suite (a failed task never hides inside an average),
//   - completed every run of the ranked task (when ranking by a task), and
//   - clears MIN_QUALITY on the UNROUNDED score (5.95 does not pass as "6.0").
function sponsorEligible(s: Scored, taskId: string | null): boolean {
  if ((s.failed_tasks ?? []).length) return false;
  const t = taskId ? s.tasks?.[taskId] : undefined;
  if (taskId) {
    if (!t) return false;
    const [done, total] = t.runs_completed.split("/").map(Number);
    if (!(done === total && total > 0)) return false;
  }
  const exact = t ? t.score_exact : s.score_exact;
  if (exact === undefined) return false; // no unrounded score baked: fail closed
  return exact >= MIN_QUALITY;
}

// Why a bid was declined. Only called for servers sponsorEligible() already rejected;
// it explains that decision and never makes one.
function declineReason(s: Scored, taskId: string | null): string {
  const failed = s.failed_tasks ?? [];
  if (failed.length) return `fails ${failed.join(", ")}`;
  const t = taskId ? s.tasks?.[taskId] : undefined;
  if (taskId && !t) return `not scored on ${taskId}`;
  if (t) {
    const [done, total] = t.runs_completed.split("/").map(Number);
    if (!(done === total && total > 0)) return `incomplete runs on ${taskId} (${t.runs_completed})`;
  }
  const exact = t ? t.score_exact : s.score_exact;
  if (exact === undefined) return "no unrounded score on record";
  return `below ${MIN_QUALITY.toFixed(1)} on ${taskId ?? "suite"} (${exact.toFixed(2)})`;
}

function verdict(s: Scored, taskId: string | null = null): string {
  const t = taskId ? s.tasks?.[taskId] : undefined;
  if ((t ?? s).runs_completed.startsWith("0/")) return "FAILED — did not complete the task";
  const failed = s.failed_tasks ?? [];
  const score = t?.score ?? s.score;
  const base = score >= 8 ? "recommended" : score >= MIN_QUALITY ? "usable" : "not recommended";
  return failed.length ? `${base}, but FAILS on: ${failed.join(", ")}` : base;
}

function taskCells(s: Scored): string {
  if (!s.tasks) return "";
  return Object.entries(s.tasks)
    .map(([id, t]) => `${id} ${t.runs_completed.startsWith("0/") ? "FAIL" : t.score}`)
    .join(" · ");
}

// Metric labels: plain-English definition on first use in a response, short form after.
const LABEL = {
  wer: "word error rate — share of words wrong, lower is better",
  kt: "share of names/numbers/jargon caught, higher is better",
  ci: "likely range of the true score",
};
const fmtWer = (v: number | null, long: boolean) =>
  v == null ? "WER n/a" : `WER ${v}${long ? ` (${LABEL.wer})` : ""}`;
const fmtKt = (v: number, long: boolean) => `key-term recall ${v}${long ? ` (${LABEL.kt})` : ""}`;
const fmtCi = (ci: [number, number], long: boolean) => `95% CI ${ci[0]}–${ci[1]}${long ? ` (${LABEL.ci})` : ""}`;
const fmtLatency = (v: number | null) => (v == null ? "latency n/a" : `latency ${v}s`);

// One ranked row. When ranking by a suite task, the metrics row is THAT task's numbers;
// suite-wide numbers appear only on the line labeled "suite".
const PASS_FAIL_LABEL = "binary pass/fail; each run's end state is read back through the server";
const fmtPassed = (runs: string, long: boolean) => `passed ${runs} runs${long ? ` (${PASS_FAIL_LABEL})` : ""}`;

function fmtLine(i: number, s: Scored, taskId: string | null, long: boolean, passFail = false): string {
  const c = CATALOG[s.id];
  const t = taskId ? s.tasks?.[taskId] : undefined;
  const where = `${c?.runs ?? "?"} · ${c?.cost ?? ""}`;
  // Pass/fail suites (e.g. todos) have no WER or key terms: show runs passed instead.
  if (passFail) {
    if (taskId && t) {
      const failed = t.runs_completed.startsWith("0/");
      return (
        `${i}. ${s.name} [id: ${s.id}] — ${taskId} ${failed ? "0/10" : `${t.score}/10`}, ${verdict(s, taskId)}\n` +
        `   ${taskId}: ${fmtPassed(t.runs_completed, long)} · ${fmtLatency(t.latency_s)}\n` +
        `   suite: ${s.score}/10 · ${fmtCi(s.ci95, long)} · tasks: ${taskCells(s)}\n` +
        `   ${where}`
      );
    }
    return (
      `${i}. ${s.name} [id: ${s.id}] — suite ${s.score}/10, ${verdict(s, taskId)}\n` +
      `   suite: ${fmtCi(s.ci95, long)} · ${fmtPassed(s.runs_completed, long)} · ${fmtLatency(s.latency_s)}\n` +
      `   tasks: ${taskCells(s)}\n` +
      `   ${where}`
    );
  }
  if (taskId && t) {
    const failed = t.runs_completed.startsWith("0/");
    return (
      `${i}. ${s.name} [id: ${s.id}] — ${taskId} ${failed ? "0/10" : `${t.score}/10`}, ${verdict(s, taskId)}\n` +
      `   ${taskId}: runs ${t.runs_completed} · ${fmtWer(t.wer, long)} · ${fmtKt(t.keyphrase_recall, long)} · ${fmtLatency(t.latency_s)}\n` +
      `   suite: ${s.score}/10 · ${fmtCi(s.ci95, long)} · tasks: ${taskCells(s)}\n` +
      `   ${where}`
    );
  }
  const cells = taskCells(s);
  return (
    `${i}. ${s.name} [id: ${s.id}] — suite ${s.score}/10, ${verdict(s, taskId)}\n` +
    `   suite: ${fmtCi(s.ci95, long)} · runs ${s.runs_completed} · ${fmtWer(s.wer, long)} · ` +
    `${fmtKt(s.keyphrase_recall, long)} · ${fmtLatency(s.latency_s)}\n` +
    (cells ? `   tasks: ${cells}\n` : "") +
    `   ${where}`
  );
}

server.tool(
  {
    name: "search_tools",
    description:
      "Find the best-scored MCP servers for a task, ranked by verified task score. Call this whenever " +
      "choosing between tools, servers, or APIs for a task, and before installing an MCP server. Returns the " +
      "top pick first, then a ranked comparison with per-task metrics, ties, and install instructions. " +
      "A sponsored offer appears only among statistically tied servers that pass the quality gate; " +
      'if a tied server\'s bid was refused, a line "Sponsored: none | declined_bid: <server_id> | reason: <why>" ' +
      "says so. Rankings are never sponsored. If the task is in no scored category, the result starts " +
      "\"No verified scores for '<task>' yet.\" followed by \"Scored: none | query: <task> | scored_categories: ...\".",
    inputSchema: z.object({
      task: z.string().describe('What you need done, e.g. "transcribe a meeting recording"'),
      local_only: z
        .boolean()
        .optional()
        .describe("Only servers that run on the user's machine (no cloud API / no data leaves the device)"),
    }),
  },
  async ({ task, local_only }) => {
    const cat = matchCategory(task);
    const suite = cat ? suiteFor(cat) : undefined;
    if (!suite) {
      // Same "Key: value | key: value" shape as the Sponsored: none line, so agents can parse it.
      const field = (v: string) => v.replace(/[|\s]+/g, " ").trim(); // keep "|" as a pure separator
      const categories = SUITES.map(
        (s) => `${s.task_category} (servers: ${s.servers.length}, tasks: ${s.tasks?.length ?? 1})`,
      ).join("; ");
      return text(
        [
          `No verified scores for '${task}' yet.`,
          `Scored: none | query: ${field(task)} | scored_categories: ${categories}`,
          "AgentPacts is a lab — we only publish scores backed by a ground-truth task suite; " +
            "new categories are added as they're scored. We won't guess.",
        ].join("\n"),
      );
    }
    const taskId = matchSuiteTask(task, suite);
    let ranked = [...suite.servers].sort((a, b) => rankScore(b, taskId) - rankScore(a, taskId));
    if (local_only) ranked = ranked.filter((s) => CATALOG[s.id]?.runs === "local");
    if (!ranked.length) return text(`No servers match those constraints in "${suite.task_category}".`);

    // Decision first: agents anchor on what they read first.
    const top = ranked[0];
    const scope = taskId ?? "suite";
    const topT = taskId ? top.tasks?.[taskId] : undefined;
    const topFailed = (topT ?? top).runs_completed.startsWith("0/");
    const c = CATALOG[top.id];
    const lines: string[] = [];
    if (topFailed || rankScore(top, taskId) < MIN_QUALITY) {
      lines.push(
        `Top pick for "${task}": none — no server${local_only ? " (local only)" : ""} clears the ` +
          `${MIN_QUALITY.toFixed(1)} quality bar on ${scope} (best: ${top.id}, ` +
          `${topFailed ? "failed" : `${rankScore(top, taskId)}/10`}).`,
      );
    } else {
      lines.push(
        `Top pick for "${task}": ${top.id} — highest ${scope} score (${rankScore(top, taskId)}/10).`,
        c ? `  install: ${c.install}` : "",
        c ? `  MCP config: ${JSON.stringify(c.mcp_config)}` : "",
      );
    }
    lines.push(
      "",
      `Category: ${suite.task_category} · suite ${suite.suite} · scored ${suite.scored_at.slice(0, 10)}`,
      taskId
        ? `Ranked by suite task "${taskId}": ${suite.tasks?.find((t) => t.id === taskId)?.desc ?? ""}. ` +
            `Per-server metrics below are for ${taskId}; the "suite" line covers all ${suite.tasks?.length ?? 1} tasks.`
        : "Ranked by overall suite score (weighted mean over all tasks); metrics below are suite-wide.",
      "",
      ...ranked.map((s, i) => fmtLine(i + 1, s, taskId, i === 0, suite.scoring === "pass_fail")),
    );

    // Ties: groups of adjacent servers that are statistically indistinguishable.
    const tiePairs = ranked.slice(1).flatMap((s, i) => (tied(ranked[i], s, taskId) ? [[ranked[i], s]] : []));
    // Two servers that both miss the quality bar are not a meaningful tie to choose between.
    const belowBar = (s: Scored) => rankScore(s, taskId) < MIN_QUALITY;
    const realTies = tiePairs.filter(([a, b]) => !(belowBar(a) && belowBar(b)));
    const bothFailed = tiePairs.filter(([a, b]) => belowBar(a) && belowBar(b));
    if (realTies.length) {
      lines.push("", `Statistical ties (${scope} scores within ${TIE_MARGIN}; difference not meaningful):`);
      for (const [a, b] of realTies)
        lines.push(`  • ${a.id} ≈ ${b.id} (${rankScore(a, taskId)} vs ${rankScore(b, taskId)})`);
    }
    if (bothFailed.length) {
      lines.push("", `Below the ${MIN_QUALITY.toFixed(1)} quality bar:`);
      for (const [a, b] of bothFailed)
        lines.push(
          taskId
            ? `  • ${a.id} and ${b.id}: both failed this task`
            : `  • ${a.id} and ${b.id}: both failed this suite (overall below ${MIN_QUALITY.toFixed(1)})`,
        );
    }

    // Sponsored slot: only among tied, quality-gated servers. Never changes scores or order.
    if (OFFERS_ENABLED) {
      const eligible = new Set(tiePairs.flat().filter((s) => sponsorEligible(s, taskId)).map((s) => s.id));
      const offer = OFFERS.filter((o) => eligible.has(o.server_id)).sort(
        (a, b) => b.bid_usd_per_selection - a.bid_usd_per_selection,
      )[0];
      if (offer) {
        lines.push(
          "",
          `Sponsored: ${offer.server_id} | bid_usd_per_selection: ${offer.bid_usd_per_selection} | ` +
            `terms: ${offer.terms} | status: ILLUSTRATIVE (seeded demo bid, no real money) | ranking_affected: no`,
        );
      } else {
        // An auction was possible (a real tie) and a tied server bid, but the gate refused it.
        // One line per declined bid; omitted entirely when no tied server bid.
        const tied = new Set(realTies.flat().map((s) => s.id));
        const declined = OFFERS.filter((o) => tied.has(o.server_id) && !eligible.has(o.server_id));
        if (declined.length) lines.push("");
        for (const o of declined) {
          const s = ranked.find((v) => v.id === o.server_id)!;
          lines.push(`Sponsored: none | declined_bid: ${o.server_id} | reason: ${declineReason(s, taskId)}`);
        }
      }
    }

    lines.push("", "Full per-server report (all tasks, failure diagnoses, method): get_scores(server_id).");
    return text(lines.join("\n").replace(/\n{3,}/g, "\n\n"));
  },
);

server.tool(
  {
    name: "get_scores",
    description:
      "Full score report for one MCP server: score with confidence interval, per-metric results, " +
      "what it got wrong (missed key terms), sample output, errors, install config, and method.",
    inputSchema: z.object({
      server_id: z.string().describe('Server id from search_tools, e.g. "sanzaru"'),
    }),
  },
  async ({ server_id }) => {
    for (const suite of SUITES) {
      const s = suite.servers.find((v) => v.id === server_id);
      if (!s) continue;
      const c = CATALOG[s.id];
      const rank = [...suite.servers].sort((a, b) => b.score - a.score).findIndex((v) => v.id === s.id) + 1;
      return text(
        [
          `${s.name} [id: ${s.id}]`,
          `Repo: ${s.repo || c?.repo || "n/a"} · backend: ${s.backend || "n/a"}`,
          `Category: ${suite.task_category} (suite ${suite.suite}) · rank ${rank}/${suite.servers.length}`,
          `Suite score: ${s.score}/10 (${fmtCi(s.ci95, true)}; run range ${s.score_range[0]}–${s.score_range[1]}) — ${verdict(s)}`,
          suite.scoring === "pass_fail"
            ? `Suite-wide: ${fmtPassed(s.runs_completed, true)} · avg ${fmtLatency(s.latency_s)}`
            : `Suite-wide: runs completed ${s.runs_completed} · ${fmtWer(s.wer, true)} · ${fmtKt(s.keyphrase_recall, true)} · avg ${fmtLatency(s.latency_s)}`,
          s.tasks
            ? "Per task:\n" +
              Object.entries(s.tasks)
                .map(
                  ([id, t]) =>
                    `  • ${id}: ${t.runs_completed.startsWith("0/") ? "FAILED" : `${t.score}/10`} ` +
                    (suite.scoring === "pass_fail"
                      ? `(passed ${t.runs_completed}${t.latency_s != null ? `, ${t.latency_s}s` : ""})`
                      : `(runs ${t.runs_completed}, WER ${t.wer ?? "n/a"}, key terms ${t.keyphrase_recall}` +
                        `${t.latency_s != null ? `, ${t.latency_s}s` : ""})`) +
                    (DIAGNOSES[s.id]?.[id] ? `\n      diagnosis: ${DIAGNOSES[s.id][id]}` : "") +
                    (t.errors.length ? `\n      error: ${t.errors[0]}` : "") +
                    (t.missed_phrases.length && !t.runs_completed.startsWith("0/")
                      ? `\n      missed: ${t.missed_phrases.join(", ")}`
                      : ""),
                )
                .join("\n")
            : s.missed_phrases.length
              ? `Missed key terms: ${s.missed_phrases.join(", ")}`
              : "Missed key terms: none",
          !s.tasks && s.errors.length ? `Errors:\n  ${s.errors.join("\n  ")}` : "",
          "",
          `Fixture: ${suite.fixture} — ${suite.fixture_desc}`,
          `Method: ${suite.method}`,
          `Scored at: ${suite.scored_at}`,
          "",
          suite.scoring === "pass_fail" ? "" : `Sample output: ${s.sample_transcript || "(none)"}`,
          "",
          c ? `Install: ${c.install}\nRequires: ${c.requires.join("; ")}\nMCP config: ${JSON.stringify(c.mcp_config)}` : "",
        ]
          .filter(Boolean)
          .join("\n"),
      );
    }
    const ids = SUITES.flatMap((s) => s.servers.map((v) => v.id));
    return text(`Unknown server_id "${server_id}". Known server ids: ${ids.join(", ")}.`);
  },
);

server.tool(
  {
    name: "list_categories",
    description: "List the task categories AgentPacts has verified scores for, with the leader in each.",
    inputSchema: z.object({}),
  },
  async () =>
    text(
      SUITES.map((s) => {
        const top = [...s.servers].sort((a, b) => b.score - a.score)[0];
        return `${s.task_category} (${s.suite}, ${s.servers.length} servers, scored ${s.scored_at.slice(0, 10)}) — leader: ${top.id} ${top.score}/10`;
      }).join("\n"),
    ),
);

export default server;
