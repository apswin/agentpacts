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
};
