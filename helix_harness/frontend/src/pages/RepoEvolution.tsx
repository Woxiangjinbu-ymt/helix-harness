import { useEffect, useState } from "react";
import {
  Activity,
  CheckCircle2,
  FlaskConical,
  FolderGit2,
  GitBranch,
  History,
  Layers3,
  Link2,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  ScanSearch,
  Sparkles,
  Wrench,
} from "lucide-react";
import { useI18n } from "../i18n/context";
import { go, useData, useRevision } from "../lib/api";
import { formatDate, num, short } from "../lib/format";
import {
  repoRsiApi,
  type RepoEvalCase,
  type RepoJob,
  type RepoListItem,
  type RepoRsiProvider,
  type RepoSummary,
  type VersionDetail,
} from "../lib/repoRsi";
import { CodeViewer } from "../components/CodeViewer";
import { CodingDirectoryPicker } from "../components/CodingDirectoryPicker";
import { DiffViewer } from "../components/DiffViewer";
import { Badge, Empty, Notice, Panel } from "../components/ui";

type TabId = "frictions" | "cases" | "evolve" | "versions";

export function RepoEvolution({ id }: { id?: string }) {
  const { t, locale } = useI18n();
  const r = t.repoRsi;
  const [rev, refresh] = useRevision();

  const reposRes = useData<{ repos: RepoListItem[] }>("repo-rsi/repos", rev);
  const providersRes = useData<{ providers: RepoRsiProvider[] }>(
    "repo-rsi/providers",
    rev,
  );

  const repos = reposRes.data?.repos || [];
  const providers = providersRes.data?.providers || [];
  const selectedId =
    id && repos.some((x) => x.repo_id === id) ? id : repos[0]?.repo_id || null;

  const detailRes = useData<RepoSummary>(
    selectedId ? `repo-rsi/repos/${encodeURIComponent(selectedId)}` : null,
    rev,
  );
  const repo = detailRes.data;

  const [tab, setTab] = useState<TabId>("frictions");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [manualPath, setManualPath] = useState("");
  const [provider, setProvider] = useState("");
  const [mode, setMode] = useState<"hybrid" | "ace" | "skilladam">("hybrid");
  const [rounds, setRounds] = useState(2);
  const [targetScore, setTargetScore] = useState(95);
  const [maxRegression, setMaxRegression] = useState(5);
  const [includeCalibrated, setIncludeCalibrated] = useState(false);
  const [exportToRepo, setExportToRepo] = useState(true);

  const [busy, setBusy] = useState<string | null>(null);
  const [banner, setBanner] = useState<{ kind: "ok" | "error"; text: string } | null>(
    null,
  );

  // 活跃后台作业（校准 / 进化）
  const [activeJob, setActiveJob] = useState<RepoJob | null>(null);

  // 草稿用例编辑状态
  const [editingCase, setEditingCase] = useState<string | null>(null);
  const [editPrompt, setEditPrompt] = useState("");
  const [editChecksJson, setEditChecksJson] = useState("[]");
  const [editRubricText, setEditRubricText] = useState("");

  // 手工新建用例表单
  const [showNewCase, setShowNewCase] = useState(false);
  const [newCaseId, setNewCaseId] = useState("");
  const [newCaseTitle, setNewCaseTitle] = useState("");
  const [newCasePrompt, setNewCasePrompt] = useState("");
  const [newCaseChecks, setNewCaseChecks] = useState(
    JSON.stringify(
      [
        {
          id: "build-check",
          type: "command",
          command: "npm run build",
          weight: 2,
          description: "生产构建通过",
        },
      ],
      null,
      2,
    ),
  );
  const [newCaseRubric, setNewCaseRubric] = useState("");
  const [newCaseFreeze, setNewCaseFreeze] = useState(false);

  // 版本快照检查
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [versionTab, setVersionTab] = useState<"diff" | "files">("diff");

  const activeVersion =
    selectedVersion ?? repo?.head.version ?? null;
  const versionDetailRes = useData<VersionDetail>(
    selectedId && activeVersion
      ? `repo-rsi/repos/${encodeURIComponent(selectedId)}/versions/${activeVersion}`
      : null,
    rev,
  );

  useEffect(() => {
    if (!provider && providers.length > 0) {
      setProvider(providers[0].id);
    }
  }, [providers, provider]);

  useEffect(() => {
    setSelectedVersion(null);
    setEditingCase(null);
    setActiveJob(null);
  }, [selectedId]);

  // 轮询运行中的后台作业，完成后自动刷新仓库状态
  useEffect(() => {
    if (!activeJob || activeJob.status !== "running") return;
    let alive = true;
    const timer = window.setInterval(() => {
      repoRsiApi
        .getJob(activeJob.job_id)
        .then((next) => {
          if (!alive) return;
          setActiveJob(next);
          if (next.status !== "running") {
            refresh();
            if (next.status === "completed") {
              setBanner({
                kind: "ok",
                text:
                  next.kind === "evolve"
                    ? r.evolveCompleted
                    : r.calibrateCompleted,
              });
            } else if (next.error) {
              setBanner({ kind: "error", text: next.error });
            }
          }
        })
        .catch(() => {});
    }, 1200);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [activeJob, refresh, r.calibrateCompleted, r.evolveCompleted]);

  async function handleRegister(path: string) {
    setBusy("register");
    setBanner(null);
    try {
      const created = await repoRsiApi.registerRepo(path.trim());
      setPickerOpen(false);
      setManualPath("");
      refresh();
      go("repo-rsi", created.meta.repo_id);
      setBanner({ kind: "ok", text: r.registeredOk(created.meta.name) });
    } catch (e) {
      setBanner({ kind: "error", text: (e as Error).message });
      throw e;
    } finally {
      setBusy(null);
    }
  }

  async function handleScanFriction(withLlm: boolean) {
    if (!selectedId) return;
    setBusy("scan");
    setBanner(null);
    try {
      const res = await repoRsiApi.scanFriction(
        selectedId,
        withLlm ? provider : undefined,
      );
      refresh();
      setBanner({
        kind: "ok",
        text: r.scanResult(res.scanned_turns, res.frictions_added),
      });
    } catch (e) {
      setBanner({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  async function handleSynthesize(frictionId: string) {
    if (!selectedId) return;
    setBusy("synth:" + frictionId);
    setBanner(null);
    try {
      const created = await repoRsiApi.createCase(selectedId, {
        friction_id: frictionId,
        provider: provider || null,
      });
      refresh();
      setTab("cases");
      setBanner({ kind: "ok", text: r.synthesizedOk(created.case_id) });
    } catch (e) {
      setBanner({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  function startEditCase(c: RepoEvalCase) {
    setEditingCase(c.case_id);
    setEditPrompt(c.prompt);
    setEditChecksJson(JSON.stringify(c.checks, null, 2));
    setEditRubricText(c.rubric.map((x) => x.criterion).join("\n"));
  }

  async function handleApproveCase(caseId: string) {
    if (!selectedId) return;
    setBusy("approve:" + caseId);
    setBanner(null);
    try {
      const parsedChecks = JSON.parse(editChecksJson);
      const rubricLines = editRubricText
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean)
        .map((criterion, idx) => ({ id: `r${idx + 1}`, criterion, weight: 1 }));
      await repoRsiApi.approveCase(selectedId, caseId, {
        prompt: editPrompt,
        checks: parsedChecks,
        rubric: rubricLines,
      });
      setEditingCase(null);
      refresh();
      setBanner({ kind: "ok", text: r.caseApproved(caseId) });
    } catch (e) {
      setBanner({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  async function handleCreateManualCase() {
    if (!selectedId || !newCaseId.trim() || !newCasePrompt.trim()) return;
    setBusy("new-case");
    setBanner(null);
    try {
      const checks = JSON.parse(newCaseChecks || "[]");
      const rubric = newCaseRubric
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean)
        .map((criterion, idx) => ({ id: `r${idx + 1}`, criterion, weight: 1 }));
      await repoRsiApi.createCase(selectedId, {
        case_id: newCaseId.trim(),
        title: newCaseTitle.trim() || newCaseId.trim(),
        prompt: newCasePrompt.trim(),
        checks,
        rubric,
        freeze: newCaseFreeze,
      });
      setShowNewCase(false);
      setNewCaseId("");
      setNewCaseTitle("");
      setNewCasePrompt("");
      setNewCaseRubric("");
      refresh();
      setBanner({ kind: "ok", text: r.caseCreated });
    } catch (e) {
      setBanner({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  async function handleCalibrate(caseId: string) {
    if (!selectedId || !provider) {
      setBanner({ kind: "error", text: r.selectProviderFirst });
      return;
    }
    setBusy("cal:" + caseId);
    setBanner(null);
    try {
      const job = await repoRsiApi.startCalibrate(selectedId, caseId, {
        provider,
        max_baseline_score: 85,
      });
      setActiveJob(job);
      setTab("evolve");
    } catch (e) {
      setBanner({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  async function handleStartEvolve() {
    if (!selectedId || !provider) {
      setBanner({ kind: "error", text: r.selectProviderFirst });
      return;
    }
    setBusy("evolve");
    setBanner(null);
    try {
      const job = await repoRsiApi.startEvolve(selectedId, {
        provider,
        optimizer_provider: provider,
        judge_provider: provider,
        mode,
        rounds,
        runs_per_case: 1,
        target_score: targetScore,
        max_regression: maxRegression,
        include_calibrated: includeCalibrated,
      });
      setActiveJob(job);
    } catch (e) {
      setBanner({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  async function handleBind(version?: number) {
    if (!selectedId) return;
    setBusy("bind");
    setBanner(null);
    try {
      const res = await repoRsiApi.bindToCoding(
        selectedId,
        version,
        exportToRepo,
      );
      refresh();
      setBanner({
        kind: "ok",
        text: r.boundOk(res.binding.version),
      });
    } catch (e) {
      setBanner({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  async function handleRollback(version: number) {
    if (!selectedId || !repo) return;
    setBusy("rollback:" + version);
    setBanner(null);
    try {
      const head = await repoRsiApi.rollback(
        selectedId,
        version,
        repo.head.revision,
      );
      setSelectedVersion(head.version);
      refresh();
      setBanner({ kind: "ok", text: r.rollbackOk(head.version) });
    } catch (e) {
      setBanner({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  const headVersionRec = repo?.versions.find(
    (v) => v.version === repo.head.version,
  );
  const frozenCount =
    repo?.cases.filter((c) => c.status === "frozen").length || 0;
  const openIssueCount =
    repo?.eit_issues.filter(
      (i) => i.status === "open" || i.status === "reopened",
    ).length || 0;

  return (
    <>
      <div className="page-title">
        <div>
          <h1>{r.title}</h1>
          <p>{r.subtitle}</p>
        </div>
        <div className="btns">
          <button
            type="button"
            className="primary"
            onClick={() => setPickerOpen(true)}
          >
            <Plus size={14} />
            {r.registerRepo}
          </button>
          <button type="button" onClick={refresh}>
            <RefreshCw size={14} />
            {t.controls.refresh}
          </button>
        </div>
      </div>

      {banner && (
        <div className={"notice " + (banner.kind === "error" ? "error" : "")}>
          {banner.kind === "ok" ? (
            <CheckCircle2 size={16} />
          ) : (
            <Activity size={16} />
          )}
          <span>{banner.text}</span>
        </div>
      )}

      {reposRes.error && <Notice error={reposRes.error} />}

      <div className="researchgrid">
        {/* 左侧仓库列表 */}
        <Panel
          title={r.managedRepos}
          icon={<FolderGit2 size={16} />}
          action={
            <span className="chip">{repos.length}</span>
          }
        >
          <div className="pad stack">
            <form
              className="row"
              style={{ gap: 6 }}
              onSubmit={(e) => {
                e.preventDefault();
                if (manualPath.trim()) void handleRegister(manualPath);
              }}
            >
              <input
                type="text"
                value={manualPath}
                onChange={(e) => setManualPath(e.target.value)}
                placeholder={r.repoPathPlaceholder}
                style={{ flex: 1, minWidth: 0 }}
              />
              <button
                type="submit"
                className="sm"
                disabled={busy === "register" || !manualPath.trim()}
              >
                {r.add}
              </button>
            </form>

            {repos.length === 0 ? (
              <Empty icon={<Sparkles size={22} />}>{r.noRepos}</Empty>
            ) : (
              <div className="stack" style={{ gap: 6 }}>
                {repos.map((item) => (
                  <button
                    key={item.repo_id}
                    type="button"
                    className={
                      "listselect repo-rsi-repo-item" +
                      (item.repo_id === selectedId ? " on" : "")
                    }
                    onClick={() => go("repo-rsi", item.repo_id)}
                  >
                    <div className="row" style={{ justifyContent: "space-between", width: "100%" }}>
                      <b className="repo-rsi-ellipsis">{item.name}</b>
                      <Badge value={`v${item.head_version}`} />
                    </div>
                    <small title={item.repo_root}>
                      {item.mean_score != null
                        ? `${num(item.mean_score)} ${t.dashboard.pts}`
                        : r.unmeasured}{" "}
                      · {r.casesShort(item.frozen_cases, item.total_cases)} ·{" "}
                      {r.issuesShort(item.open_issues)}
                    </small>
                    {item.coding_binding && (
                      <small style={{ color: "var(--ok, #16a34a)" }}>
                        <Link2 size={11} style={{ verticalAlign: "middle" }} />{" "}
                        Coding v{item.coding_binding.version}
                      </small>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        </Panel>

        {/* 右侧仓库详情工作区 */}
        <div className="stack">
          {!selectedId ? (
            <Panel title={r.selectRepoTitle} icon={<Sparkles size={16} />}>
              <Empty icon={<FolderGit2 size={24} />}>{r.selectRepoHint}</Empty>
            </Panel>
          ) : !repo ? (
            <Notice error={detailRes.error} />
          ) : (
            <>
              {/* 顶部摘要 + F24 绑定栏 */}
              <Panel
                title={`${repo.meta.name} · Harness v${repo.head.version}`}
                icon={<Sparkles size={16} />}
                action={
                  <div className="btns">
                    <button
                      type="button"
                      className="sm"
                      disabled={busy === "scan"}
                      onClick={() => void handleScanFriction(false)}
                    >
                      <ScanSearch size={13} />
                      {r.scanCoding}
                    </button>
                    <button
                      type="button"
                      className="sm primary"
                      disabled={busy === "bind"}
                      onClick={() => void handleBind(repo.head.version)}
                    >
                      <Link2 size={13} />
                      {r.bindActive(repo.head.version)}
                    </button>
                  </div>
                }
              >
                <div className="pad stack">
                  <div className="facts">
                    <div>
                      <small>{r.repoRoot}</small>
                      <strong
                        title={repo.meta.repo_root}
                        className="repo-rsi-path"
                      >
                        {repo.meta.repo_root}
                      </strong>
                    </div>
                    <div>
                      <small>{r.currentScore}</small>
                      <strong>
                        {headVersionRec?.mean_score != null
                          ? `${num(headVersionRec.mean_score)} ${t.dashboard.pts}`
                          : r.unmeasured}
                      </strong>
                    </div>
                    <div>
                      <small>{r.frozenCasesLabel}</small>
                      <strong>
                        {frozenCount} / {repo.cases.length}
                      </strong>
                    </div>
                    <div>
                      <small>{r.openIssuesLabel}</small>
                      <strong>{openIssueCount}</strong>
                    </div>
                    <div>
                      <small>{r.codingBindingLabel}</small>
                      <strong>
                        {repo.coding_binding ? (
                          <Badge value={`Coding v${repo.coding_binding.version}`} />
                        ) : (
                          r.notBound
                        )}
                      </strong>
                    </div>
                    <div>
                      <small>{r.snapshotDigest}</small>
                      <strong>{short(repo.head.digest)}</strong>
                    </div>
                  </div>

                  <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
                    <div className="tabs">
                      <button
                        type="button"
                        className={tab === "frictions" ? "on" : ""}
                        onClick={() => setTab("frictions")}
                      >
                        {r.tabFrictions} ({repo.eit_issues.length})
                      </button>
                      <button
                        type="button"
                        className={tab === "cases" ? "on" : ""}
                        onClick={() => setTab("cases")}
                      >
                        {r.tabCases} ({repo.cases.length})
                      </button>
                      <button
                        type="button"
                        className={tab === "evolve" ? "on" : ""}
                        onClick={() => setTab("evolve")}
                      >
                        {r.tabEvolve}
                        {activeJob?.status === "running" ? " ●" : ""}
                      </button>
                      <button
                        type="button"
                        className={tab === "versions" ? "on" : ""}
                        onClick={() => setTab("versions")}
                      >
                        {r.tabVersions} ({repo.versions.length})
                      </button>
                    </div>

                    <label className="row" style={{ gap: 6, fontSize: 12, color: "var(--text-muted)" }}>
                      <input
                        type="checkbox"
                        checked={exportToRepo}
                        onChange={(e) => setExportToRepo(e.target.checked)}
                      />
                      {r.exportMewcode}
                    </label>
                  </div>
                </div>
              </Panel>

              {/* Tab 1: EIT 台账与交互摩擦 */}
              {tab === "frictions" && (
                <div className="stack">
                  <Panel
                    title={r.eitTitle}
                    icon={<Wrench size={16} />}
                    action={
                      provider ? (
                        <button
                          type="button"
                          className="sm"
                          disabled={busy === "scan"}
                          onClick={() => void handleScanFriction(true)}
                        >
                          <Sparkles size={13} />
                          {r.scanWithLlm}
                        </button>
                      ) : null
                    }
                  >
                    <div className="pad stack">
                      {repo.eit_issues.length === 0 ? (
                        <Empty>{r.noIssues}</Empty>
                      ) : (
                        repo.eit_issues.map((issue) => (
                          <div key={issue.issue_id} className="repo-rsi-card">
                            <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
                              <div className="row" style={{ gap: 8 }}>
                                <code>{issue.issue_id}</code>
                                <b>{issue.title}</b>
                              </div>
                              <div className="row" style={{ gap: 6 }}>
                                <span className="chip">{issue.category}</span>
                                <span className="chip">×{issue.occurrences}</span>
                                <Badge value={issue.status} />
                              </div>
                            </div>
                            <p className="hint" style={{ margin: "6px 0 0" }}>
                              {issue.description}
                            </p>
                            {issue.linked_cases.length > 0 && (
                              <div className="tags" style={{ marginTop: 6 }}>
                                {issue.linked_cases.map((cid) => (
                                  <span key={cid}>{cid}</span>
                                ))}
                              </div>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  </Panel>

                  <Panel title={r.frictionsTitle} icon={<ScanSearch size={16} />}>
                    <div className="pad stack">
                      {repo.frictions.length === 0 ? (
                        <Empty>{r.noFrictions}</Empty>
                      ) : (
                        repo.frictions.map((f) => (
                          <div key={f.id} className="repo-rsi-card">
                            <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
                              <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                                <Badge value={f.kind} />
                                <span className="chip">{f.category}</span>
                                <span className="chip">{f.severity}</span>
                                {f.issue_id && <code>{f.issue_id}</code>}
                              </div>
                              <button
                                type="button"
                                className="sm primary"
                                disabled={busy === "synth:" + f.id}
                                onClick={() => void handleSynthesize(f.id)}
                              >
                                <FlaskConical size={13} />
                                {r.synthesizeCase}
                              </button>
                            </div>
                            <p style={{ margin: "8px 0 4px", fontSize: 13 }}>{f.summary}</p>
                            <small className="hint">
                              session {short(f.session_id)} · turn {short(f.turn_id)} ·{" "}
                              {formatDate(f.created_at, locale)}
                            </small>
                          </div>
                        ))
                      )}
                    </div>
                  </Panel>
                </div>
              )}

              {/* Tab 2: 仓库回归评测集 */}
              {tab === "cases" && (
                <Panel
                  title={r.casesTitle}
                  icon={<FlaskConical size={16} />}
                  action={
                    <button
                      type="button"
                      className="sm primary"
                      onClick={() => setShowNewCase((v) => !v)}
                    >
                      <Plus size={13} />
                      {r.addManualCase}
                    </button>
                  }
                >
                  <div className="pad stack">
                    {showNewCase && (
                      <div className="repo-rsi-card fields">
                        <div className="two">
                          <label>
                            <span>Case ID</span>
                            <input
                              value={newCaseId}
                              onChange={(e) => setNewCaseId(e.target.value)}
                              placeholder="kline-layout-main"
                            />
                          </label>
                          <label>
                            <span>{r.caseTitleLabel}</span>
                            <input
                              value={newCaseTitle}
                              onChange={(e) => setNewCaseTitle(e.target.value)}
                              placeholder={r.caseTitlePlaceholder}
                            />
                          </label>
                        </div>
                        <label>
                          <span>{r.casePromptLabel}</span>
                          <textarea
                            rows={3}
                            value={newCasePrompt}
                            onChange={(e) => setNewCasePrompt(e.target.value)}
                            placeholder={r.casePromptPlaceholder}
                          />
                        </label>
                        <label>
                          <span>Checks JSON (command / file_assert / playwright)</span>
                          <textarea
                            rows={6}
                            value={newCaseChecks}
                            onChange={(e) => setNewCaseChecks(e.target.value)}
                          />
                        </label>
                        <label>
                          <span>{r.rubricLinesLabel}</span>
                          <textarea
                            rows={3}
                            value={newCaseRubric}
                            onChange={(e) => setNewCaseRubric(e.target.value)}
                            placeholder={r.rubricPlaceholder}
                          />
                        </label>
                        <div className="row" style={{ justifyContent: "space-between" }}>
                          <label className="row" style={{ gap: 6 }}>
                            <input
                              type="checkbox"
                              checked={newCaseFreeze}
                              onChange={(e) => setNewCaseFreeze(e.target.checked)}
                            />
                            <span>{r.freezeImmediately}</span>
                          </label>
                          <div className="btns">
                            <button type="button" className="sm" onClick={() => setShowNewCase(false)}>
                              {t.controls.cancel}
                            </button>
                            <button
                              type="button"
                              className="sm primary"
                              disabled={busy === "new-case" || !newCaseId.trim() || !newCasePrompt.trim()}
                              onClick={() => void handleCreateManualCase()}
                            >
                              {r.saveCase}
                            </button>
                          </div>
                        </div>
                      </div>
                    )}

                    {repo.cases.length === 0 ? (
                      <Empty>{r.noCases}</Empty>
                    ) : (
                      repo.cases.map((c) => {
                        const isEditing = editingCase === c.case_id;
                        return (
                          <div key={c.case_id} className="repo-rsi-card">
                            <div className="row" style={{ justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                              <div className="row" style={{ gap: 8 }}>
                                <b>{c.title}</b>
                                <code>{c.case_id}</code>
                                <Badge value={c.status} />
                                {c.baseline_score != null && (
                                  <span className="chip">
                                    v1: {num(c.baseline_score)} {t.dashboard.pts}
                                  </span>
                                )}
                              </div>
                              <div className="btns">
                                {c.status !== "frozen" && !isEditing && (
                                  <button
                                    type="button"
                                    className="sm"
                                    onClick={() => startEditCase(c)}
                                  >
                                    {r.reviewEdit}
                                  </button>
                                )}
                                {c.status !== "frozen" && (
                                  <button
                                    type="button"
                                    className="sm primary"
                                    disabled={busy === "cal:" + c.case_id || !provider}
                                    onClick={() => void handleCalibrate(c.case_id)}
                                  >
                                    <Play size={12} />
                                    {r.calibrateAndFreeze}
                                  </button>
                                )}
                              </div>
                            </div>

                            {isEditing ? (
                              <div className="fields" style={{ marginTop: 10 }}>
                                <label>
                                  <span>{r.casePromptLabel}</span>
                                  <textarea
                                    rows={3}
                                    value={editPrompt}
                                    onChange={(e) => setEditPrompt(e.target.value)}
                                  />
                                </label>
                                <label>
                                  <span>Checks JSON</span>
                                  <textarea
                                    rows={5}
                                    value={editChecksJson}
                                    onChange={(e) => setEditChecksJson(e.target.value)}
                                  />
                                </label>
                                <label>
                                  <span>{r.rubricLinesLabel}</span>
                                  <textarea
                                    rows={3}
                                    value={editRubricText}
                                    onChange={(e) => setEditRubricText(e.target.value)}
                                  />
                                </label>
                                <div className="btns">
                                  <button
                                    type="button"
                                    className="sm"
                                    onClick={() => setEditingCase(null)}
                                  >
                                    {t.controls.cancel}
                                  </button>
                                  <button
                                    type="button"
                                    className="sm primary"
                                    disabled={busy === "approve:" + c.case_id}
                                    onClick={() => void handleApproveCase(c.case_id)}
                                  >
                                    <CheckCircle2 size={13} />
                                    {r.confirmApprove}
                                  </button>
                                </div>
                              </div>
                            ) : (
                              <>
                                <p style={{ margin: "8px 0", fontSize: 13 }}>{c.prompt}</p>
                                <div className="tags">
                                  <span>commit: {short(c.base_commit)}</span>
                                  <span>checks: {c.checks.length}</span>
                                  <span>rubric: {c.rubric.length}</span>
                                  <span>
                                    weights: {c.weights.checks}/{c.weights.trajectory}/{c.weights.rubric}
                                  </span>
                                </div>
                              </>
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>
                </Panel>
              )}

              {/* Tab 3: 启动进化与实时进度 */}
              {tab === "evolve" && (
                <div className="stack">
                  <Panel title={r.evolveConfigTitle} icon={<GitBranch size={16} />}>
                    <div className="pad fields">
                      <div className="two">
                        <label>
                          <span>{r.providerLabel}</span>
                          <select
                            value={provider}
                            onChange={(e) => setProvider(e.target.value)}
                          >
                            {providers.length === 0 && (
                              <option value="">{r.noProviders}</option>
                            )}
                            {providers.map((p) => (
                              <option key={p.id} value={p.id}>
                                {p.provider} · {p.model} ({p.source})
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span>{r.algorithmMode}</span>
                          <select
                            value={mode}
                            onChange={(e) =>
                              setMode(e.target.value as "hybrid" | "ace" | "skilladam")
                            }
                          >
                            <option value="hybrid">
                              hybrid · ACE + SkillAdam + RRSI + AWM
                            </option>
                            <option value="ace">ace · Playbook Incremental</option>
                            <option value="skilladam">
                              skilladam · EIT + Adaptive Budget
                            </option>
                          </select>
                        </label>
                      </div>
                      <div className="two">
                        <label>
                          <span>{r.roundsLabel}</span>
                          <input
                            type="number"
                            min={1}
                            max={10}
                            value={rounds}
                            onChange={(e) => setRounds(Number(e.target.value) || 1)}
                          />
                        </label>
                        <label>
                          <span>{r.targetScoreLabel}</span>
                          <input
                            type="number"
                            min={1}
                            max={100}
                            value={targetScore}
                            onChange={(e) => setTargetScore(Number(e.target.value) || 95)}
                          />
                        </label>
                      </div>
                      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
                        <label className="row" style={{ gap: 6 }}>
                          <input
                            type="checkbox"
                            checked={includeCalibrated}
                            onChange={(e) => setIncludeCalibrated(e.target.checked)}
                          />
                          <span>{r.includeCalibrated}</span>
                        </label>
                        <label className="row" style={{ gap: 6 }}>
                          <span>{r.maxRegressionLabel}</span>
                          <input
                            type="number"
                            min={0}
                            max={50}
                            style={{ width: 68 }}
                            value={maxRegression}
                            onChange={(e) => setMaxRegression(Number(e.target.value) || 0)}
                          />
                        </label>
                        <button
                          type="button"
                          className="primary"
                          disabled={
                            busy === "evolve" ||
                            activeJob?.status === "running" ||
                            !provider ||
                            (frozenCount === 0 && !includeCalibrated)
                          }
                          onClick={() => void handleStartEvolve()}
                        >
                          <Play size={14} />
                          {r.startEvolve}
                        </button>
                      </div>
                    </div>
                  </Panel>

                  {activeJob && (
                    <Panel
                      title={`${activeJob.kind === "evolve" ? r.liveEvolution : r.liveCalibration} · ${activeJob.job_id}`}
                      icon={<Activity size={16} />}
                      action={<Badge value={activeJob.status} />}
                    >
                      <div className="pad stack">
                        {activeJob.error && <Notice error={activeJob.error} />}
                        <div className="repo-rsi-log">
                          {activeJob.events.length === 0 ? (
                            <div className="hint">{t.controls.loading}</div>
                          ) : (
                            activeJob.events.map((ev, i) => (
                              <div key={i} className="repo-rsi-log-row">
                                <code>{ev.kind}</code>
                                <span>{JSON.stringify(ev.data)}</span>
                              </div>
                            ))
                          )}
                        </div>
                        {activeJob.result && (
                          <pre className="repo-rsi-json">
                            {JSON.stringify(activeJob.result, null, 2)}
                          </pre>
                        )}
                      </div>
                    </Panel>
                  )}

                  <Panel title={r.recentRunsTitle} icon={<History size={16} />}>
                    <div className="pad stack">
                      {repo.recent_runs.length === 0 ? (
                        <Empty>{r.noRuns}</Empty>
                      ) : (
                        repo.recent_runs.map((run) => (
                          <div key={run.run_id} className="repo-rsi-card row" style={{ justifyContent: "space-between" }}>
                            <div>
                              <b>{run.run_id}</b>
                              <small className="hint" style={{ display: "block" }}>
                                {run.optimizer_name} · {formatDate(run.created_at, locale)}
                              </small>
                            </div>
                            <div className="row" style={{ gap: 6 }}>
                              {run.stop_reason && <span className="chip">{run.stop_reason}</span>}
                              <Badge value={run.status} />
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </Panel>
                </div>
              )}

              {/* Tab 4: 版本谱系、Playbook/Tool Guidance Diff 与回滚 */}
              {tab === "versions" && (
                <div className="researchgrid">
                  <Panel title={r.versionsTitle} icon={<Layers3 size={16} />}>
                    <div className="lineage">
                      {repo.versions.map((v) => {
                        const isHead = v.version === repo.head.version;
                        const dot = isHead
                          ? "active"
                          : v.status === "accepted"
                            ? "accepted"
                            : v.status === "rejected"
                              ? "rejected"
                              : "";
                        return (
                          <button
                            key={v.version}
                            type="button"
                            className={
                              "listselect" + (activeVersion === v.version ? " on" : "")
                            }
                            onClick={() => setSelectedVersion(v.version)}
                          >
                            <div className="lineage-item">
                              <span className="lineage-branch">
                                <span className={"lineage-dot " + dot} />
                              </span>
                              <span>
                                <b>
                                  v{v.version}
                                  {isHead ? " · " + t.versions.activeTag : ""}
                                </b>
                                <small>
                                  {v.status} ·{" "}
                                  {v.mean_score != null
                                    ? `${num(v.mean_score)} ${t.dashboard.pts}`
                                    : r.unmeasured}
                                </small>
                              </span>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  </Panel>

                  <Panel
                    title={activeVersion ? `Harness v${activeVersion}` : r.versionsTitle}
                    icon={<GitBranch size={16} />}
                    action={
                      versionDetailRes.data &&
                      (versionDetailRes.data.version.status === "baseline" ||
                        versionDetailRes.data.version.status === "accepted") ? (
                        <div className="btns">
                          <button
                            type="button"
                            className="sm"
                            disabled={busy === "bind"}
                            onClick={() =>
                              void handleBind(versionDetailRes.data!.version.version)
                            }
                          >
                            <Link2 size={13} />
                            {r.bindThisVersion}
                          </button>
                          {versionDetailRes.data.version.version !== repo.head.version && (
                            <button
                              type="button"
                              className="sm"
                              disabled={
                                busy ===
                                "rollback:" + versionDetailRes.data.version.version
                              }
                              onClick={() =>
                                void handleRollback(
                                  versionDetailRes.data!.version.version,
                                )
                              }
                            >
                              <RotateCcw size={13} />
                              {r.rollbackHere}
                            </button>
                          )}
                        </div>
                      ) : null
                    }
                  >
                    {!versionDetailRes.data ? (
                      <Notice error={versionDetailRes.error} />
                    ) : (
                      <div className="pad stack">
                        <div className="tags">
                          <span>playbook: {versionDetailRes.data.playbook_count}</span>
                          <span>skills: {versionDetailRes.data.skills.length}</span>
                          <span>
                            tool overlays:{" "}
                            {Object.keys(versionDetailRes.data.tool_overlays).length}
                          </span>
                          <span>
                            tool hooks: {versionDetailRes.data.tool_hooks.length}
                          </span>
                        </div>
                        <p className="hint" style={{ margin: 0 }}>
                          {versionDetailRes.data.version.summary}
                        </p>
                        <div className="tabs">
                          <button
                            type="button"
                            className={versionTab === "diff" ? "on" : ""}
                            onClick={() => setVersionTab("diff")}
                          >
                            {t.versions.tabChanges}
                          </button>
                          <button
                            type="button"
                            className={versionTab === "files" ? "on" : ""}
                            onClick={() => setVersionTab("files")}
                          >
                            {t.versions.tabFiles}
                          </button>
                        </div>
                        {versionTab === "diff" ? (
                          versionDetailRes.data.diff ? (
                            <DiffViewer text={versionDetailRes.data.diff} />
                          ) : (
                            <Empty>{r.noDiff}</Empty>
                          )
                        ) : (
                          <CodeViewer files={versionDetailRes.data.files} />
                        )}
                      </div>
                    )}
                  </Panel>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {pickerOpen && (
        <CodingDirectoryPicker
          onOpen={handleRegister}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </>
  );
}
