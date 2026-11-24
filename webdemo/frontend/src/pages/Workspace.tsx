import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  ArrowUp,
  BookOpen,
  ChevronDown,
  ChevronRight,
  FileCode2,
  FileDiff,
  FolderGit2,
  RotateCcw,
  SlidersHorizontal,
  Sparkles,
  Square,
  X,
} from "lucide-react";
import { useI18n } from "../i18n/context";
import { api, go, useData, useJournal } from "../lib/api";
import { parseDiff } from "../lib/diff";
import { elapsed, num, short, terminal } from "../lib/format";
import { useWorkspace } from "../lib/workspaceContext";
import { CodeViewer } from "../components/CodeViewer";
import { DiffViewer } from "../components/DiffViewer";
import { TraceTimeline } from "../components/TraceTimeline";
import { Badge, CopyButton, Empty, Modal, Notice } from "../components/ui";
import type { Provider, Repo, TaskDetail, Trace, Version } from "../types";

type DrawerTab = "Diff" | "Files" | "Source" | "Tools";

export function Workspace({ id }: { id?: string }) {
  const { t } = useI18n();
  const {
    repo,
    setRepo,
    rev,
    refresh,
    registerOpen,
    setRegisterOpen,
  } = useWorkspace();

  const repos = useData<Repo[]>("repositories", rev);
  const models = useData<Provider[]>("providers");
  const versions = useData<Version[]>("versions", rev);

  const [provider, setProvider] = useState("demo-mock");
  const [version, setVersion] = useState("default");
  const [prompt, setPrompt] = useState("");
  const [turns, setTurns] = useState(12);
  const [timeoutSec, setTimeoutSec] = useState(90);
  const [showBudget, setShowBudget] = useState(false);

  const [busy, setBusy] = useState(false);
  const [regBusy, setRegBusy] = useState(false);
  const [err, setErr] = useState("");
  const [slug, setSlug] = useState("");

  const [drawerOpen, setDrawerOpen] = useState(Boolean(id));
  const [tab, setTab] = useState<DrawerTab>(id ? "Diff" : "Source");
  const [selectedSourceFile, setSelectedSourceFile] = useState("");
  const [stepsExpanded, setStepsExpanded] = useState(true);

  const detail = useData<TaskDetail>(id ? "tasks/" + id : null, rev);
  const d = detail.data;

  // Keep drawer closed on fresh New Task canvas for serene full-width Agent App feel;
  // open automatically when inspecting a session so diffs/files are one glance away.
  useEffect(() => {
    setDrawerOpen(Boolean(id));
    setTab(id ? "Diff" : "Source");
    setStepsExpanded(true);
  }, [id]);

  // Sync repo selector with opened task's case when valid
  useEffect(() => {
    if (d?.case && repos.data?.some((r) => r.id === d.case)) {
      setRepo(d.case);
    }
  }, [d?.case, repos.data, setRepo]);

  const activeRepoId = id && d?.case ? d.case : repo;
  const repoContents = useData<Record<string, string>>(
    activeRepoId ? "repositories/" + activeRepoId + "/contents" : null,
  );
  const repoFilesFallback = useData<string[]>(
    activeRepoId && repoContents.error
      ? "repositories/" + activeRepoId + "/files"
      : null,
  );
  const repoFileList = useMemo(
    () =>
      repoContents.data
        ? Object.keys(repoContents.data)
        : repoFilesFallback.data || [],
    [repoContents.data, repoFilesFallback.data],
  );

  const journal = useJournal("tasks", d?.job);
  const last = journal.events.at(-1);
  useEffect(() => {
    if (
      last &&
      [
        "run_completed",
        "run_cancelled",
        "run_failed",
        "run_interrupted",
      ].includes(last.event_type)
    ) {
      refresh();
    }
  }, [last?.event_id, refresh]);

  const traces: Trace[] = useMemo(
    () =>
      journal.events
        .filter((e) => e.data.seq != null)
        .map((e) => e.data as unknown as Trace),
    [journal.events],
  );
  const events =
    traces.length > (d?.events.length || 0) ? traces : d?.events || [];

  const replay = d?.replays?.[0];
  const replayJournal = useJournal("evaluations", replay?.id);
  const replayLast = replayJournal.events.at(-1);
  useEffect(() => {
    if (
      replayLast &&
      ["run_completed", "run_failed", "run_cancelled"].includes(
        replayLast.event_type,
      )
    ) {
      refresh();
    }
  }, [replayLast?.event_id, refresh]);

  const selectedRepo = (repos.data || []).find((r) => r.id === repo);

  const diffFiles = useMemo(() => parseDiff(d?.diff || ""), [d?.diff]);
  const totalAdd = useMemo(
    () => diffFiles.reduce((s, f) => s + f.added, 0),
    [diffFiles],
  );
  const totalDel = useMemo(
    () => diffFiles.reduce((s, f) => s + f.removed, 0),
    [diffFiles],
  );

  const toolEvents = useMemo(
    () =>
      events.filter((e) =>
        ["tool_use", "tool_result", "permission"].includes(e.type),
      ),
    [events],
  );

  async function runTask() {
    if (busy || !selectedRepo) return;
    setErr("");
    setBusy(true);
    try {
      const r = await api<{ id: string }>("tasks/run", {
        repo,
        provider,
        version,
        turns,
        timeout: timeoutSec,
        prompt: prompt || selectedRepo.statement,
      });
      setPrompt("");
      go("workspace", r.id);
      refresh();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function cancelTask() {
    if (!d?.job) return;
    try {
      await api("tasks/" + d.job + "/cancel", {});
      refresh();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function registerRepo() {
    if (!slug.trim() || regBusy) return;
    setRegBusy(true);
    setErr("");
    try {
      const r = await api<{ id: string }>("repositories", { name: slug.trim() });
      setRepo(r.id);
      setSlug("");
      setRegisterOpen(false);
      refresh();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setRegBusy(false);
    }
  }

  async function rescore() {
    if (!d) return;
    setErr("");
    try {
      await api("evaluations/run", { task_id: d.id });
      refresh();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  function openReview(targetTab: DrawerTab = "Diff") {
    setTab(targetTab);
    setDrawerOpen(true);
  }

  const errMsg =
    err || repos.error || models.error || versions.error || detail.error;

  const isRunning = Boolean(d?.job && !terminal(d.status));

  return (
    <div className={"agent-workspace" + (drawerOpen ? " with-drawer" : "")}>
      {/* PRIMARY SERENE CONVERSATION CANVAS */}
      <section className="agent-stage">
        {/* Subtle floating top-right drawer trigger when drawer is closed */}
        {!drawerOpen && (
          <div className="agent-floating-actions">
            {diffFiles.length > 0 && (
              <button
                type="button"
                className="sm"
                onClick={() => openReview("Diff")}
              >
                <FileDiff size={13} />
                <span>{t.workspace.review}</span>
                <span className="diff-stat-add">+{totalAdd}</span>
                <span className="diff-stat-del">-{totalDel}</span>
              </button>
            )}
            <button
              type="button"
              className="sm"
              onClick={() => openReview(id ? "Files" : "Source")}
            >
              <FileCode2 size={13} />
              <span>{id ? t.workspace.tabFiles : t.workspace.tabSource}</span>
            </button>
          </div>
        )}

        {/* SCROLLABLE THREAD */}
        <div className="agent-thread-scroll">
          <div className="agent-thread-inner">
            {errMsg && <Notice error={errMsg} />}

            {!id ? (
              /* CALM EMPTY / NEW TASK WELCOME */
              <div className="agent-empty-stage">
                <div className="agent-greeting">
                  <div className="agent-greeting-pill">
                    <Sparkles size={13} />
                    <span>
                      {selectedRepo
                        ? `${selectedRepo.name} · ${selectedRepo.kind}`
                        : "Helix Agent"}
                    </span>
                  </div>
                  <h1>{t.workspace.composerTitle}</h1>
                  <p>{t.workspace.composerSubtitle}</p>
                </div>

                {selectedRepo?.statement && (
                  <div className="agent-spec-card">
                    <div className="agent-spec-head">
                      <span className="row" style={{ gap: 6 }}>
                        <BookOpen size={14} />
                        <span>
                          {t.workspace.benchmarkSpec} · {selectedRepo.name}
                        </span>
                      </span>
                      <div className="row" style={{ gap: 6 }}>
                        {repoFileList.length > 0 && (
                          <button
                            type="button"
                            className="sm ghost"
                            onClick={() => openReview("Source")}
                          >
                            <FileCode2 size={13} />
                            {t.workspace.tabSource} ({repoFileList.length})
                          </button>
                        )}
                        {selectedRepo.case && (
                          <button
                            type="button"
                            className="preset-btn"
                            onClick={() => setPrompt(selectedRepo.statement)}
                          >
                            {t.workspace.useBenchmarkTask}
                            <ArrowRight size={12} />
                          </button>
                        )}
                      </div>
                    </div>
                    <pre className="agent-spec-body">
                      {selectedRepo.statement}
                    </pre>
                  </div>
                )}
              </div>
            ) : (
              /* RECORDED / LIVE AGENT CONVERSATION THREAD */
              <div className="agent-conversation">
                {/* 1. User Message Bubble (Right-aligned like classic Agent Apps) */}
                {d?.request?.prompt && (
                  <div className="agent-user-row">
                    <div className="agent-user-bubble">
                      <p>{d.request.prompt}</p>
                    </div>
                  </div>
                )}

                {/* 2. Agent Activity & Result Turn (Left-aligned, airy) */}
                <div className="agent-assistant-block">
                  {/* Collapsible "Worked for X s >" trigger */}
                  <div className="agent-worked-row">
                    <button
                      type="button"
                      className="agent-worked-toggle"
                      onClick={() => setStepsExpanded((v) => !v)}
                    >
                      {journal.connected || isRunning ? (
                        <span className="live-dot" />
                      ) : null}
                      <span>
                        {journal.connected || isRunning
                          ? t.workspace.workingNow
                          : d?.duration_ms != null
                            ? t.workspace.workedFor(elapsed(d.duration_ms))
                            : t.workspace.agentTrajectory}
                      </span>
                      <span className="agent-worked-meta">
                        · {t.workspace.eventsCount(events.length)}
                      </span>
                      {stepsExpanded ? (
                        <ChevronDown size={14} />
                      ) : (
                        <ChevronRight size={14} />
                      )}
                    </button>

                    {d && (
                      <div className="row" style={{ gap: 6 }}>
                        <Badge value={d.mode} />
                        <Badge value={d.status} />
                      </div>
                    )}
                  </div>

                  {/* Structured Activity Timeline */}
                  {stepsExpanded && (
                    <div className="agent-steps-box">
                      {events.length ? (
                        <TraceTimeline
                          events={events}
                          showFilters
                          live={isRunning && journal.connected}
                        />
                      ) : !d ? (
                        <Notice />
                      ) : (
                        <Empty>{t.workspace.waitingForEvents}</Empty>
                      )}
                    </div>
                  )}

                  {/* Inline Changes Summary Card (matches reference "N files changed +X -Y > [Review]") */}
                  {diffFiles.length > 0 && (
                    <div className="agent-changes-card">
                      <button
                        type="button"
                        className="agent-changes-left"
                        onClick={() => openReview("Diff")}
                      >
                        <span className="agent-changes-count">
                          {t.workspace.filesChanged(diffFiles.length)}
                        </span>
                        <span className="diff-stat-add">+{totalAdd}</span>
                        <span className="diff-stat-del">-{totalDel}</span>
                        <ChevronRight size={14} style={{ color: "var(--text-faint)" }} />
                      </button>
                      <button
                        type="button"
                        className="sm"
                        onClick={() => openReview("Diff")}
                      >
                        <FileDiff size={13} />
                        {t.workspace.review}
                      </button>
                    </div>
                  )}

                  {/* Inline Independent Evaluation Verdict Card */}
                  {d && (
                    <div className="agent-eval-card">
                      <div className="agent-eval-main">
                        <div className="score">
                          <strong>{num(d.receipt?.score)}</strong>
                          <span>/ 100</span>
                        </div>
                        <div className="agent-eval-info">
                          <div className="agent-eval-verdict">
                            {d.receipt?.scope === "public_checks"
                              ? d.receipt.passed
                                ? t.workspace.publicTestsPassed
                                : t.workspace.publicTestsFailed
                              : d.receipt
                                ? t.workspace.independentDockerEval
                                : t.workspace.waitingForReceipt}
                          </div>
                          <div className="agent-eval-sub">
                            <span>
                              {t.workspace.duration} {elapsed(d.duration_ms)}
                            </span>
                            <span>·</span>
                            <span>
                              {t.workspace.tokens}{" "}
                              {d.usage &&
                              d.usage.input_tokens + d.usage.output_tokens
                                ? num(
                                    d.usage.input_tokens + d.usage.output_tokens,
                                  )
                                : "—"}
                            </span>
                            {d.session && (
                              <>
                                <span>·</span>
                                <span className="row" style={{ gap: 4 }}>
                                  <code>{short(d.session)}</code>
                                  <CopyButton text={d.session} compact />
                                </span>
                              </>
                            )}
                          </div>
                        </div>
                      </div>

                      <div className="agent-eval-actions">
                        {d.receipt?.score != null && (
                          <>
                            <button
                              type="button"
                              className="sm"
                              onClick={rescore}
                              disabled={!!replay && !terminal(replay.status)}
                            >
                              <RotateCcw size={12} />
                              {replay && !terminal(replay.status)
                                ? t.workspace.replaying
                                : t.workspace.replayScore}
                            </button>
                            {replay && (
                              <span className="hint">
                                {replay.status === "completed"
                                  ? t.workspace.receiptMatched
                                  : replay.status}
                              </span>
                            )}
                          </>
                        )}
                      </div>

                      {d.error && (
                        <p
                          className="danger"
                          style={{ margin: "10px 0 0", fontSize: 12 }}
                        >
                          {d.error}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ALWAYS-DOCKED FLOATING BOTTOM COMPOSER (Claude Desktop / Codex / Antigravity style) */}
        <div className="agent-composer-dock">
          <div className="agent-composer-shell">
            {showBudget && (
              <div className="agent-budget-bar">
                <label className="agent-budget-field">
                  <span>{t.workspace.maxTurns}</span>
                  <input
                    type="number"
                    min={1}
                    max={24}
                    value={turns}
                    onChange={(e) => setTurns(+e.target.value)}
                  />
                </label>
                <label className="agent-budget-field">
                  <span>{t.workspace.timeoutSeconds}</span>
                  <input
                    type="number"
                    min={5}
                    max={180}
                    value={timeoutSec}
                    onChange={(e) => setTimeoutSec(+e.target.value)}
                  />
                </label>
                <span className="hint" style={{ marginLeft: "auto" }}>
                  {t.workspace.shortcutHint}
                </span>
              </div>
            )}

            <textarea
              aria-label={t.workspace.taskInstructions}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                  e.preventDefault();
                  runTask();
                }
              }}
              rows={2}
              placeholder={
                selectedRepo?.statement.split("\n")[0] ||
                t.workspace.taskInstructionsPlaceholder
              }
            />

            <div className="agent-composer-bar">
              <div className="agent-composer-pills">
                <div className="composer-select-pill">
                  <FolderGit2 size={13} />
                  <select
                    aria-label={t.workspace.sourceRepository}
                    value={repo}
                    onChange={(e) => {
                      setRepo(e.target.value);
                      setPrompt("");
                    }}
                  >
                    {repos.data?.map((r) => (
                      <option value={r.id} key={r.id}>
                        {r.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="composer-select-pill">
                  <select
                    aria-label={t.workspace.modelProvider}
                    value={provider}
                    onChange={(e) => setProvider(e.target.value)}
                  >
                    {models.data?.map((p) => (
                      <option value={p.id} key={p.id}>
                        {p.mode} · {p.model}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="composer-select-pill">
                  <select
                    aria-label={t.workspace.harnessVersion}
                    value={version}
                    onChange={(e) => setVersion(e.target.value)}
                  >
                    <option value="default">{t.workspace.defaultHarness}</option>
                    {versions.data?.map((v) => (
                      <option key={v.id} value={v.id}>
                        v{v.version} · {v.base}
                      </option>
                    ))}
                  </select>
                </div>

                <button
                  type="button"
                  className={
                    "composer-pill-btn" + (showBudget ? " active" : "")
                  }
                  onClick={() => setShowBudget((v) => !v)}
                  title={t.workspace.settings}
                >
                  <SlidersHorizontal size={12} />
                  <span>
                    {turns}t · {timeoutSec}s
                  </span>
                </button>
              </div>

              <div className="agent-composer-actions">
                {isRunning && (
                  <button
                    type="button"
                    className="sm danger-btn"
                    onClick={cancelTask}
                  >
                    <Square size={11} />
                    {t.workspace.cancelTask}
                  </button>
                )}
                <button
                  type="button"
                  className="composer-send-btn"
                  aria-label={t.workspace.runTask}
                  title={t.workspace.runTask}
                  onClick={runTask}
                  disabled={busy || !selectedRepo}
                >
                  <ArrowUp size={16} strokeWidth={2.4} />
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ON-DEMAND RIGHT REVIEW & ARTIFACTS DRAWER */}
      {drawerOpen && (
        <aside className="agent-drawer">
          <div className="agent-drawer-head">
            <div className="tabs">
              {(
                [
                  ["Diff", t.workspace.tabDiff],
                  ["Files", t.workspace.tabFiles],
                  ["Source", t.workspace.tabSource],
                  ["Tools", t.workspace.tabTools],
                ] as [DrawerTab, string][]
              ).map(([k, label]) => (
                <button
                  type="button"
                  key={k}
                  className={tab === k ? "on" : ""}
                  onClick={() => setTab(k)}
                >
                  {label}
                </button>
              ))}
            </div>
            <button
              type="button"
              className="iconbtn"
              aria-label={t.workspace.hideInspector}
              title={t.workspace.hideInspector}
              onClick={() => setDrawerOpen(false)}
            >
              <X size={15} />
            </button>
          </div>

          <div className="agent-drawer-body">
            {tab === "Diff" ? (
              <DiffViewer text={d?.diff || ""} />
            ) : tab === "Files" ? (
              <CodeViewer
                files={d?.files || {}}
                ariaLabel={t.workspace.artifactFileAria}
                emptyText={t.workspace.noSealedFiles}
              />
            ) : tab === "Source" ? (
              <CodeViewer
                files={repoContents.data || {}}
                selectedFile={selectedSourceFile}
                onSelectFile={setSelectedSourceFile}
                ariaLabel={t.workspace.sourceFileAria}
                emptyText={t.workspace.noSourceFiles}
              />
            ) : toolEvents.length ? (
              <TraceTimeline events={toolEvents} />
            ) : (
              <Empty>{t.workspace.noToolCalls}</Empty>
            )}
          </div>
        </aside>
      )}

      {/* REGISTER PUBLIC REPOSITORY MODAL */}
      <Modal
        open={registerOpen}
        title={t.workspace.registerRepository}
        onClose={() => setRegisterOpen(false)}
      >
        <div className="fields">
          <label>
            {t.workspace.publishedGitProject}
            <input
              autoFocus
              value={slug}
              placeholder={t.workspace.projectDirPlaceholder}
              onChange={(e) => setSlug(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  registerRepo();
                }
              }}
            />
          </label>
          <p className="hint" style={{ margin: 0 }}>
            {t.workspace.registerHint}
          </p>
          {err && <Notice error={err} />}
          <div className="btns" style={{ justifyContent: "flex-end" }}>
            <button type="button" onClick={() => setRegisterOpen(false)}>
              {t.controls.cancel}
            </button>
            <button
              type="button"
              className="primary"
              disabled={!slug.trim() || regBusy}
              onClick={registerRepo}
            >
              {regBusy ? t.workspace.registering : t.workspace.registerProject}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
