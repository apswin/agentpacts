// How an agent installs each scored server once it has picked one.
// Scores live in scores.ts (generated); this file is static metadata only.

export type CatalogEntry = {
  repo: string;
  runs: "local" | "cloud-api";
  cost: string;
  requires: string[];
  install: string;
  mcp_config: Record<string, unknown>;
};

export const CATALOG: Record<string, CatalogEntry> = {
  sanzaru: {
    repo: "https://github.com/TJC-LP/sanzaru",
    runs: "cloud-api",
    cost: "OpenAI per-minute transcription pricing",
    requires: ["OPENAI_API_KEY", "Python 3.10+", "uv"],
    install: 'claude mcp add sanzaru -e OPENAI_API_KEY=<your key> -e SANZARU_MEDIA_PATH=$HOME/media -- uvx "sanzaru[all]"',
    mcp_config: {
      command: "uvx",
      args: ["sanzaru[all]"],
      env: { OPENAI_API_KEY: "<your key>", SANZARU_MEDIA_PATH: "/abs/path/to/media" },
    },
  },
  hakanai: {
    repo: "https://github.com/hakanai-ai/agent-voice-mcp",
    runs: "local",
    cost: "free (runs on your machine)",
    requires: ["whisper.cpp server on :2022 (uvx voice-mode service install whisper)", "Python 3.10+", "mcp<2"],
    install:
      "git clone https://github.com/hakanai-ai/agent-voice-mcp && cd agent-voice-mcp && uv venv && uv pip install 'mcp<2'",
    mcp_config: {
      command: "uv",
      args: ["run", "--directory", "/abs/path/agent-voice-mcp", "python", "server.py"],
      env: { WHISPER_URL: "http://localhost:2022" },
    },
  },
  localstt: {
    repo: "https://github.com/SmartLittleApps/local-stt-mcp",
    runs: "local",
    cost: "free (runs on your machine)",
    requires: ["whisper-cli on PATH", "models/ggml-base.en.bin", "Node 18+", "install path without spaces"],
    install:
      "git clone https://github.com/SmartLittleApps/local-stt-mcp && cd local-stt-mcp/mcp-server && npm install && npm run build && npm run setup:models",
    mcp_config: {
      command: "node",
      args: ["/abs/path/local-stt-mcp/mcp-server/dist/index.js"],
    },
  },
  todoist: {
    repo: "https://github.com/Doist/todoist-ai",
    runs: "cloud-api",
    cost: "free tier available (Todoist account)",
    requires: ["TODOIST_API_KEY", "Node 18+"],
    install: "claude mcp add todoist -e TODOIST_API_KEY=<your token> -- npx -y @doist/todoist-mcp",
    mcp_config: { command: "npx", args: ["-y", "@doist/todoist-mcp"], env: { TODOIST_API_KEY: "<your token>" } },
  },
  trello: {
    repo: "https://github.com/delorenj/mcp-server-trello",
    runs: "cloud-api",
    cost: "free tier available (Trello account)",
    requires: ["TRELLO_API_KEY", "TRELLO_TOKEN", "Node 18+"],
    install:
      "claude mcp add trello -e TRELLO_API_KEY=<your key> -e TRELLO_TOKEN=<your token> -- npx -y @delorenj/mcp-server-trello",
    mcp_config: {
      command: "npx",
      args: ["-y", "@delorenj/mcp-server-trello"],
      env: { TRELLO_API_KEY: "<your key>", TRELLO_TOKEN: "<your token>" },
    },
  },
  airtable: {
    repo: "https://github.com/domdomegg/airtable-mcp-server",
    runs: "cloud-api",
    cost: "free tier available (Airtable account)",
    requires: ["AIRTABLE_API_KEY (personal access token with data.records and schema.bases scopes)", "Node 18+"],
    install: "claude mcp add airtable -e AIRTABLE_API_KEY=<your token> -- npx -y airtable-mcp-server",
    mcp_config: { command: "npx", args: ["-y", "airtable-mcp-server"], env: { AIRTABLE_API_KEY: "<your token>" } },
  },
  notion: {
    repo: "https://github.com/makenotion/notion-mcp-server",
    runs: "cloud-api",
    cost: "free tier available (Notion account)",
    requires: ["NOTION_TOKEN (internal integration secret)", "pages shared with the integration", "Node 18+"],
    install: "claude mcp add notion -e NOTION_TOKEN=<your secret> -- npx -y @notionhq/notion-mcp-server",
    mcp_config: { command: "npx", args: ["-y", "@notionhq/notion-mcp-server"], env: { NOTION_TOKEN: "<your secret>" } },
  },
};

// Root causes found by reading each failing run's trace and the server's source.
// Shown by get_scores next to the measured result: what broke, and how the vendor can fix it.
export const DIAGNOSES: Record<string, Record<string, string>> = {
  hakanai: {
    "voice-memo":
      'Uploads via `curl -F "file=@<path>"`; curl treats "," and ";" in that argument as separators, so any ' +
      'filename containing them (e.g. "Standup, Oct 17.m4a") fails with an empty "Whisper error:". ' +
      "The error is returned as normal text, not isError. Fix: upload with an HTTP library or copy to a safe temp name.",
    "long-meeting":
      "Audio over 600s is split into 10-minute chunks; for the first chunk (60s silent lead-in) the whisper.cpp " +
      'server returned only repeated "[silence]", dropping ~9 minutes of speech. Fix: trim leading silence / ' +
      "enable VAD before chunking, and detect all-[silence] output as a failure.",
  },
  localstt: {
    "voice-memo":
      "The tool schema advertises auto_convert default=true, but index.ts passes raw arguments to the handler " +
      "without applying zod defaults, so .m4a/.mp3 go unconverted to whisper-cli (\"read_audio_data\" error). " +
      "Fix: parse args with TranscribeArgsSchema before calling the handler.",
    "long-meeting":
      "Same root cause as voice-memo: .m4a input is never converted unless the caller explicitly passes auto_convert=true.",
  },
  todoist: {
    "busy-list-newest":
      "find-tasks returns 10 items by default, oldest first; in a 31-item project the newest item is not in the first " +
      "page. The response does report hasMore with a cursor, so an agent that pages through finds it.",
  },
  trello: {
    "nlp-date": 'dueDate only accepts ISO 8601; the phrase "tomorrow at 5pm" is rejected with HTTP 400.',
    recurring: "Trello cards have no recurrence field through this server; the item is stored as a one-time card.",
    "error-semantics":
      "This server has no delete tool, only archive. Moving an archived card to the Done list succeeds and returns " +
      "the card, so an agent completing a deleted card is told it worked.",
  },
  airtable: {
    "nlp-date": 'The Due field rejects "tomorrow at 5pm" ("Cannot parse date value"); dates must be ISO 8601.',
    recurring: "Airtable has no recurrence field type; the item is stored as a one-time record.",
  },
  notion: {
    "nlp-date": 'Date properties only accept ISO 8601; "tomorrow at 5pm" fails validation (HTTP 400).',
    recurring: "Notion database pages have no recurrence property through the API; the item is stored as a one-time page.",
    "search-by-description":
      "Notion search is indexed asynchronously: a page created seconds earlier is not returned. In our diagnostic the " +
      "page appeared between 10 and 30 seconds after creation.",
  },
};
