import { api } from "./api";

export type RepoRsiProvider = {
  id: string;
  provider: string;
  model: string;
  protocol: string;
  source: string;
};

export type CodingBinding = {
  worktree_id: string;
  repo_id: string;
  version: number;
  revision: number;
  digest: string;
  updated: string;
};

export type RepoListItem = {
  repo_id: string;
  name: string;
  repo_root: string;
  head_version: number;
  head_revision: number;
  head_digest: string;
  mean_score: number | null;
  open_issues: number;
  frozen_cases: number;
  total_cases: number;
  coding_binding: CodingBinding | null;
};

export type EitIssue = {
  issue_id: string;
  category: string;
  title: string;
  description: string;
  status: string;
  occurrences: number;
  linked_cases: string[];
  attempted_fixes: {
    version: number;
    hypothesis: string;
    outcome: string;
    score_delta: number;
    at: string;
  }[];
  last_seen_at: string;
  updated_at: string;
};

export type FrictionEvent = {
  id: string;
  session_id: string;
  turn_id: string;
  kind: string;
  category: string;
  severity: string;
  summary: string;
  evidence: Record<string, unknown>;
  base_commit: string;
  issue_id: string | null;
  created_at: string;
};

export type RepoEvalCase = {
  case_id: string;
  title: string;
  status: string;
  base_commit: string;
  base_patch: string;
  prompt: string;
  checks: Record<string, unknown>[];
  rubric: { id: string; criterion: string; weight?: number }[];
  weights: { checks: number; trajectory: number; rubric: number };
  baseline_score: number | null;
  origin_friction_id: string | null;
  case_digest: string;
  created_at: string;
  updated_at: string;
};

export type RepoHarnessVersion = {
  version: number;
  digest: string;
  parent: number | null;
  status: string;
  mean_score: number | null;
  per_case: Record<string, number>;
  proposal: Record<string, unknown> | null;
  critic: Record<string, unknown> | null;
  summary: string;
  created_at: string;
};

export type RepoSummary = {
  meta: {
    repo_id: string;
    repo_root: string;
    name: string;
    created_at: string;
  };
  head: {
    version: number;
    revision: number;
    digest: string;
    updated_at: string;
  };
  versions: RepoHarnessVersion[];
  eit_issues: EitIssue[];
  cases: RepoEvalCase[];
  frictions: FrictionEvent[];
  head_history: {
    revision: number;
    from_version: number | null;
    to_version: number;
    action: string;
    reason: string;
    recorded_at: string;
  }[];
  recent_runs: {
    run_id: string;
    optimizer_name: string;
    status: string;
    stop_reason: string | null;
    eit_variance: number;
    created_at: string;
    ended_at: string | null;
  }[];
  coding_binding: CodingBinding | null;
};

export type VersionDetail = {
  version: RepoHarnessVersion;
  files: Record<string, string>;
  diff: string;
  playbook_count: number;
  tool_overlays: Record<string, string>;
  tool_hooks: Record<string, unknown>[];
  skills: string[];
};

export type RepoJob = {
  job_id: string;
  repo_id: string;
  kind: "evolve" | "calibrate";
  mode?: string;
  case_id?: string;
  status: "running" | "completed" | "failed";
  started_at: string;
  ended_at: string | null;
  events: { time: string; kind: string; data: Record<string, unknown> }[];
  result: Record<string, unknown> | null;
  error: string | null;
};

export const repoRsiApi = {
  providers: () => api<{ providers: RepoRsiProvider[] }>("repo-rsi/providers"),
  listRepos: () => api<{ repos: RepoListItem[] }>("repo-rsi/repos"),
  registerRepo: (repo_root: string, name = "") =>
    api<RepoSummary>("repo-rsi/repos", { repo_root, name }),
  getRepo: (repoId: string) =>
    api<RepoSummary>(`repo-rsi/repos/${encodeURIComponent(repoId)}`),
  getVersion: (repoId: string, version: number) =>
    api<VersionDetail>(
      `repo-rsi/repos/${encodeURIComponent(repoId)}/versions/${version}`,
    ),
  scanFriction: (repoId: string, provider?: string) =>
    api<{
      scanned_turns: number;
      frictions_added: number;
      frictions: FrictionEvent[];
    }>(`repo-rsi/repos/${encodeURIComponent(repoId)}/friction/scan`, {
      provider: provider || null,
    }),
  createCase: (repoId: string, payload: Record<string, unknown>) =>
    api<RepoEvalCase>(
      `repo-rsi/repos/${encodeURIComponent(repoId)}/cases`,
      payload,
    ),
  approveCase: (
    repoId: string,
    caseId: string,
    payload: {
      prompt?: string;
      checks?: Record<string, unknown>[];
      rubric?: { id: string; criterion: string; weight?: number }[];
    },
  ) =>
    api<RepoEvalCase>(
      `repo-rsi/repos/${encodeURIComponent(repoId)}/cases/${encodeURIComponent(caseId)}/approve`,
      payload,
    ),
  startCalibrate: (
    repoId: string,
    caseId: string,
    payload: { provider: string; max_baseline_score?: number },
  ) =>
    api<RepoJob>(
      `repo-rsi/repos/${encodeURIComponent(repoId)}/cases/${encodeURIComponent(caseId)}/calibrate/start`,
      payload,
    ),
  startEvolve: (
    repoId: string,
    payload: {
      provider: string;
      optimizer_provider?: string;
      judge_provider?: string;
      mode: "hybrid" | "ace" | "skilladam";
      rounds: number;
      runs_per_case: number;
      target_score: number;
      max_regression: number;
      include_calibrated: boolean;
    },
  ) =>
    api<RepoJob>(
      `repo-rsi/repos/${encodeURIComponent(repoId)}/evolve/start`,
      payload,
    ),
  getJob: (jobId: string) =>
    api<RepoJob>(`repo-rsi/jobs/${encodeURIComponent(jobId)}`),
  listJobs: (repoId: string) =>
    api<{ jobs: RepoJob[] }>(
      `repo-rsi/repos/${encodeURIComponent(repoId)}/jobs`,
    ),
  rollback: (
    repoId: string,
    target_version: number,
    expected_revision?: number,
    reason = "Web 主动回滚",
  ) =>
    api<{ version: number; revision: number; digest: string }>(
      `repo-rsi/repos/${encodeURIComponent(repoId)}/rollback`,
      { target_version, expected_revision, reason },
    ),
  bindToCoding: (
    repoId: string,
    version?: number,
    export_to_repo = false,
  ) =>
    api<{ binding: CodingBinding; exported: Record<string, unknown> | null }>(
      `repo-rsi/repos/${encodeURIComponent(repoId)}/bind`,
      { version: version ?? null, export_to_repo },
    ),
};
