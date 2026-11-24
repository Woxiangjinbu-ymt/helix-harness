export type Mode = "Mock" | "Real";
export type Receipt = {
  score: number | null;
  status: string;
  passed?: boolean;
  scope?: string;
  artifact_digest?: string;
  session_id?: string;
  trace_digest?: string;
};
export type Trace = {
  type: string;
  seq: number;
  session_id: string;
  tool?: string;
  tool_id?: number;
  file?: string;
  status?: string;
  is_error?: boolean;
  elapsed_ms?: number;
  turn?: number;
  model_id?: string;
  input_tokens?: number;
  output_tokens?: number;
  decision?: string;
};
export type Task = {
  id: string;
  job?: string;
  case: string;
  mode: Mode;
  status: string;
  date: string;
  score: number | null;
  version: number;
  model?: string;
  duration_ms?: number;
  usage?: { input_tokens: number; output_tokens: number };
  session?: string;
  cell?: string;
  study?: string;
  cost?: number | null;
};
export type TaskDetail = Task & {
  events: Trace[];
  files: Record<string, string>;
  diff: string;
  receipt: Receipt | null;
  replays?: { id: string; status: string }[];
  request?: { prompt: string };
  runtime_status?: string;
  error?: string;
};
export type Case = {
  case: string;
  score: number;
  cost: number | null;
  duration_ms: number;
  tasks: string[];
  samples: {
    task: string;
    index: number;
    score: number;
    duration_ms: number;
  }[];
};
export type Version = {
  id: string;
  study: string;
  base: string;
  session: string | null;
  mode: Mode;
  version: number;
  parent: number | null;
  date: string;
  active: boolean;
  score: number | null;
  decision: string;
  hypothesis: string | null;
  paths: string[];
  digest: string;
  cases: Case[];
  model: string;
  prediction?: {
    metric: string;
    before: number;
    after: number;
    supported: boolean;
  };
  duration_ms: number | null;
  cost: number | null;
};
export type Run = {
  id: string;
  study: string;
  base: string;
  session: string;
  mode: Mode;
  status: string;
  version: number;
  score: number | null;
  date: string;
  reason?: string;
  job?: string;
  config: { runs: number; rounds: number; target: number };
};
export type Overview = {
  engine: string;
  benchmark: string;
  benchmark_tasks: number;
  tasks: Task[];
  versions: Version[];
  evolutions: Run[];
};
export type Repo = {
  id: string;
  name: string;
  kind: string;
  case: string | null;
  statement: string;
};
export type Provider = { id: string; model: string; mode: Mode };
export type EventRow = {
  event_id: number;
  run_id: string;
  event_type: string;
  timestamp: string;
  data: Record<string, unknown>;
};
export type Metrics = {
  test_pass: number | null;
  completion: number | null;
  full_score: number | null;
  tokens: number | null;
  cost: number | null;
  duration_ms: number | null;
};
export type Compare = {
  before: Version;
  after: Version;
  cases: { case: string; before: number; after: number; delta: number }[];
  diff: string;
  regression_rate: number | null;
  old_metrics: Metrics;
  new_metrics: Metrics;
};
export type Finding = {
  case: string;
  gap: string;
  score: number;
  task: string;
  session: string;
  turns: number;
  writes: number;
  tool_errors: number;
  public_checks: number;
  events: Record<string, number[]>;
};
export type Round = Compare & { findings: Finding[] };
export type Evolution = Run & {
  baseline: Version | null;
  trial: Version | null;
  rounds: Round[];
  findings: Finding[];
  error?: string;
};
