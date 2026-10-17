// Shape of a harness report (harness/score.py output), one per task suite.

export type TaskResult = {
  score: number;
  score_exact?: number; // unrounded; used for gates
  runs_completed: string;
  wer: number | null;
  keyphrase_recall: number;
  missed_phrases: string[];
  latency_s: number | null;
  sample_transcript: string;
  errors: string[];
};

export type Scored = {
  id: string;
  name: string;
  repo: string;
  backend: string;
  score: number;
  score_exact?: number; // unrounded; used for gates
  ci95: [number, number];
  score_range: [number, number];
  runs_completed: string;
  wer: number | null;
  keyphrase_recall: number;
  missed_phrases: string[];
  latency_s: number | null;
  sample_transcript: string;
  errors: string[];
  failed_tasks?: string[];
  tasks?: Record<string, TaskResult>;
};

export type Suite = {
  task_category: string;
  suite: string;
  fixture: string;
  fixture_desc: string;
  method: string;
  scored_at: string;
  tasks?: { id: string; desc: string; weight: number }[];
  servers: Scored[];
};
