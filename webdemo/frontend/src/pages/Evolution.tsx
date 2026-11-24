import { useEffect, useState } from "react";
import {
  Activity,
  Check,
  Clock3,
  FlaskConical,
  GitBranch,
  GitCompareArrows,
  History,
  Layers3,
  Play,
  ScanSearch,
  Square,
} from "lucide-react";
import { useI18n } from "../i18n/context";
import {
  api,
  go,
  useData,
  useJournal,
  useRevision,
} from "../lib/api";
import { elapsed, formatDate, num, short, terminal } from "../lib/format";
import { DiffViewer } from "../components/DiffViewer";
import { Badge, Empty, LinkButton, Notice, Panel } from "../components/ui";
import type {
  Evolution as Report,
  EventRow,
  Finding,
  Provider,
  Run,
  Version,
} from "../types";

const phaseEvents = [
  ["trial_started", "trial_reused", "terminal"],
  ["evaluation_started", "evaluation_completed"],
  ["failure_analysis_started", "failure_analysis_completed"],
  ["optimization_started", "optimization_completed", "version_created"],
  ["validation_started", "validation_completed"],
  ["version_accepted", "version_rejected", "rollback_completed"],
];

export function Findings({ items }: { items: Finding[] }) {
  const { t } = useI18n();
  const groups = items.reduce<Record<string, number>>(
    (o, f) => ({ ...o, [f.gap]: (o[f.gap] || 0) + 1 }),
    {},
  );
  return items.length ? (
    <>
      <div className="tags">
        {Object.entries(groups).map(([g, n]) => (
          <span key={g}>
            {g.replaceAll("_", " ")} · {n}
          </span>
        ))}
      </div>
      {items.map((f) => (
        <details className="finding" key={f.session}>
          <summary>
            <span>{f.case}</span>
            <span>
              {f.score} {t.dashboard.pts}
            </span>
          </summary>
          <p>
            {t.evolution.findingSummary(
              f.gap.replaceAll("_", " "),
              f.turns,
              f.writes,
              f.public_checks,
            )}
          </p>
          <small>
            {t.evolution.evidenceEvents(
              short(f.session),
              Object.values(f.events).flat().join(", ") ||
                t.evolution.noneRecorded,
            )}
          </small>
          <div>
            <LinkButton onClick={() => go("workspace", f.task)}>
              {t.evolution.inspectTrajectory}
            </LinkButton>
          </div>
        </details>
      ))}
    </>
  ) : (
    <Empty>{t.evolution.noBelowTarget}</Empty>
  );
}

export function Evolution({ id }: { id?: string }) {
  const { t, locale } = useI18n();
  const [rev, refresh] = useRevision();
  const runs = useData<Run[]>("evolution", rev);
  const vs = useData<Version[]>("versions", rev);
  const providers = useData<Provider[]>("providers");
  const bench = useData<{ id: string }[]>("benchmarks");

  const [provider, setProvider] = useState("demo-mock");
  const [version, setVersion] = useState("default");
  const [rounds, setRounds] = useState(2);
  const [runsPer, setRuns] = useState(1);
  const [target, setTarget] = useState(100);
  const [newRun, showNew] = useState(!id);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, selectRound] = useState<number | null>(null);

  useEffect(() => {
    selectRound(null);
    showNew(!id);
  }, [id]);

  const report = useData<Report>(id ? "evolution/" + id : null, rev);
  const r = report.data;
  const stream = useJournal("evolution", r?.job);

  const lifecycle = stream.events.filter(
    (e) =>
      phaseEvents.some((p) => p.includes(e.event_type)) ||
      [
        "evolution_completed",
        "run_completed",
        "run_failed",
        "run_cancelled",
      ].includes(e.event_type),
  );

  const pending = lifecycle
    .filter((e) =>
      [
        "version_created",
        "evaluation_completed",
        "version_accepted",
        "version_rejected",
        "run_completed",
        "run_failed",
        "run_cancelled",
      ].includes(e.event_type),
    )
    .at(-1);

  useEffect(() => {
    if (pending) refresh();
  }, [pending?.event_id, refresh]);

  const roundIndex = selected ?? Math.max((r?.rounds.length || 1) - 1, 0);
  const round = r?.rounds[roundIndex];
  const selectable = (vs.data || []).filter(
    (v) => v.active && /^[0-9a-f]{32}$/.test(v.study),
  );

  async function start() {
    setErr("");
    setBusy(true);
    try {
      const res = await api<{ id: string }>("evolution/start", {
        provider,
        version,
        rounds,
        runs: runsPer,
        target,
        benchmark: bench.data?.[0]?.id,
      });
      go("evolution", res.id);
      refresh();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!id) return;
    try {
      await api("evolution/" + id + "/cancel", {});
      refresh();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  const error =
    err || runs.error || vs.error || providers.error || report.error;

  const currentRound = round?.after.version;
  const scope = stream.events.filter((e) => {
    if (e.data.round && round && Number(e.data.round) !== roundIndex + 1)
      return false;
    const v = Number(e.data.version);
    return !currentRound || !v || v === 1 || v === currentRound;
  });

  const hits = (i: number) =>
    scope.filter(
      (e) =>
        phaseEvents[i].includes(e.event_type) &&
        (i > 1 || !e.data.version || e.data.version === 1),
    );

  const trialCases = Object.values(
    scope
      .filter((e) => e.event_type === "terminal" && e.data.version === 1)
      .reduce<Record<string, string>>(
        (o, e) => ({ ...o, [String(e.data.cell)]: String(e.data.status) }),
        {},
      ),
  );
  const trialCount = r?.trial?.cases.length || trialCases.length;
  const terminalCells = scope.filter((e) => e.event_type === "terminal");

  const usage = new Map<string, number>();
  stream.events
    .filter((e) => e.event_type === "usage")
    .forEach((e) =>
      usage.set(
        String(e.data.cell),
        Number(e.data.input_tokens) + Number(e.data.output_tokens),
      ),
    );
  const tokens = [...usage.values()].reduce((a, b) => a + b, 0);

  const timeRange = (events: EventRow[]) =>
    events.length
      ? formatDate(events[0].timestamp, locale) +
        (events.length > 1
          ? " · " +
            elapsed(
              Date.parse(events.at(-1)!.timestamp) -
                Date.parse(events[0].timestamp),
            )
          : "")
      : t.evolution.timeNotRecorded;

  return (
    <>
      <div className="page-title">
        <div>
          <h1>{t.evolution.title}</h1>
          <p>{t.evolution.subtitle}</p>
        </div>
        <button
          type="button"
          className="primary"
          onClick={() => showNew(!newRun)}
        >
          <Play size={13} />
          {t.evolution.newEvolution}
        </button>
      </div>

      {error && <Notice error={error} />}

      {newRun && (
        <Panel
          title={t.evolution.startAnEvolution}
          icon={<FlaskConical size={16} />}
          cls="formpanel"
        >
          <div className="fields pad">
            <div className="formrow">
              <label>
                {t.evolution.benchmark}
                <select aria-label={t.evolution.benchmark}>
                  {bench.data?.map((b) => (
                    <option key={b.id}>{b.id}</option>
                  ))}
                </select>
              </label>
              <label>
                {t.evolution.testedAgent}
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                >
                  {providers.data?.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.mode} · {p.model}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t.evolution.startingHarness}
                <select
                  value={version}
                  onChange={(e) => setVersion(e.target.value)}
                >
                  <option value="default">{t.evolution.newLineage}</option>
                  {selectable.map((v) => (
                    <option value={v.id} key={v.id}>
                      v{v.version} · {v.base}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="formrow">
              <label>
                {t.evolution.roundLimit}
                <input
                  type="number"
                  min={1}
                  max={5}
                  value={rounds}
                  onChange={(e) => setRounds(+e.target.value)}
                />
              </label>
              <label>
                {t.evolution.samplesPerCase}
                <input
                  type="number"
                  min={1}
                  max={3}
                  value={runsPer}
                  onChange={(e) => setRuns(+e.target.value)}
                />
              </label>
              <label>
                {t.evolution.targetScore}
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={target}
                  onChange={(e) => setTarget(+e.target.value)}
                />
              </label>
            </div>
            <div className="btns">
              <button
                type="button"
                className="primary"
                disabled={busy || !bench.data}
                onClick={start}
              >
                <Play size={13} />
                {busy ? t.evolution.starting : t.evolution.startEvolutionBtn}
              </button>
              <span className="hint">{t.evolution.cellBudgetHint}</span>
            </div>
          </div>
        </Panel>
      )}

      <div className="researchgrid">
        <div className="stack">
          <Panel title={t.evolution.historyTitle} icon={<History size={16} />}>
            <div className="historyscroll">
              {runs.data?.map((run) => (
                <button
                  type="button"
                  key={run.id}
                  className={"listselect " + (run.id === id ? "on" : "")}
                  onClick={() => go("evolution", run.id)}
                >
                  <span>
                    <b>{short(run.session)}</b>
                    <small>
                      {run.mode} · {formatDate(run.date, locale)}
                    </small>
                  </span>
                  <div className="row" style={{ gap: 6 }}>
                    {run.score != null && (
                      <span className="mono" style={{ fontSize: 12 }}>
                        {num(run.score)}
                      </span>
                    )}
                    <Badge value={run.status} />
                  </div>
                </button>
              ))}
              {!runs.data?.length && (
                <Empty>{t.evolution.noEvolutionRuns}</Empty>
              )}
            </div>
          </Panel>
        </div>

        <div className="stack">
          {!r ? (
            id ? (
              <Notice error={report.error} />
            ) : (
              <Panel title={t.evolution.title} icon={<GitBranch size={16} />}>
                <Empty>{t.evolution.selectRunPrompt}</Empty>
              </Panel>
            )
          ) : (
            <>
              <Panel
                title={t.evolution.runPrefix + short(r.session)}
                icon={<GitBranch size={16} />}
                action={
                  <div className="row">
                    {stream.connected && (
                      <span className="live">
                        <span className="live-dot" />
                        {t.evolution.liveTag}
                      </span>
                    )}
                    <Badge value={r.mode} />
                    <Badge value={r.status} />
                  </div>
                }
              >
                <div className="facts pad">
                  {[
                    [t.evolution.benchmark, "repo-python-v1"],
                    [t.evolution.reference, "v" + r.version],
                    [t.evolution.rounds, r.rounds.length + " / " + r.config.rounds],
                    [t.evolution.score, num(r.score)],
                    [t.evolution.reportedTokens, tokens ? num(tokens) : "—"],
                  ].map(([k, v]) => (
                    <div key={k}>
                      <small>{k}</small>
                      <strong>{v}</strong>
                    </div>
                  ))}
                </div>

                <div className="stages">
                  {t.evolution.phases.map((label, i) => {
                    const es = hits(i);
                    const archived =
                      !!r.trial &&
                      (i <= 1 ||
                        (!!round && round.after.decision !== "pending"));
                    const observed = es.length > 0 || archived;
                    return (
                      <div
                        key={label}
                        className={"stage-step " + (observed ? "observed" : "")}
                      >
                        <div className="stage-node-row">
                          <span className="stage-dot">
                            {observed && <Check size={11} strokeWidth={3} />}
                          </span>
                          <span className="stage-line" />
                        </div>
                        <div className="stage-label">{label}</div>
                        <small>
                          {es.length
                            ? timeRange(es)
                            : archived
                              ? t.evolution.archivedTimeUnavailable
                              : t.evolution.pendingSkipped}
                        </small>
                      </div>
                    );
                  })}
                </div>

                <div className="pad btns" style={{ borderTop: "1px solid var(--border-subtle)" }}>
                  {r.reason && (
                    <span className="hint">
                      {t.evolution.stopReason} · {r.reason.replaceAll("_", " ")}
                    </span>
                  )}
                  {r.error && <span className="danger">{r.error}</span>}
                  {r.job && !terminal(r.status) && (
                    <button
                      type="button"
                      className="sm danger-btn"
                      onClick={cancel}
                    >
                      <Square size={11} />
                      {t.evolution.cancelEvolution}
                    </button>
                  )}
                </div>
              </Panel>

              <Panel title={t.evolution.trialAndEval} icon={<Activity size={16} />}>
                <div className="pad hint">
                  {t.evolution.trialReposSummary(
                    trialCount,
                    r.trial
                      ? r.trial.cases.filter((c) => c.score === 100).length
                      : undefined,
                    r.trial
                      ? r.trial.cases.filter((c) => c.score < 100).length
                      : undefined,
                    terminalCells.length || undefined,
                  )}
                </div>
                {r.trial && (
                  <div className="tablewrap">
                    <table>
                      <thead>
                        <tr>
                          <th>{t.evolution.referenceCase}</th>
                          <th>{t.evolution.score}</th>
                          <th>{t.evolution.trajectory}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {r.trial.cases.map((c) => (
                          <tr key={c.case}>
                            <td className="mono">{c.case}</td>
                            <td>
                              <div className="score-bar-cell">
                                <span className="mono">{num(c.score)}</span>
                                <span className="mini-bar">
                                  <i style={{ width: `${c.score}%` }} />
                                </span>
                              </div>
                            </td>
                            <td>
                              <LinkButton
                                onClick={() => go("workspace", c.tasks[0])}
                              >
                                {t.evolution.inspectRun}
                              </LinkButton>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Panel>

              {!!r.rounds.length && (
                <div className="roundpick">
                  <span>{t.evolution.iteration}</span>
                  {r.rounds.map((x, i) => (
                    <button
                      type="button"
                      className={x === round ? "current" : ""}
                      key={x.after.id}
                      onClick={() => selectRound(i)}
                    >
                      {t.evolution.roundLabel(i + 1, x.after.version)}
                    </button>
                  ))}
                </div>
              )}

              <Panel title={t.evolution.failureAnalysis} icon={<ScanSearch size={16} />}>
                <div className="pad">
                  <Findings items={round?.findings || r.findings} />
                </div>
              </Panel>

              {round && (
                <>
                  <Panel
                    title={t.evolution.strategyProposal}
                    icon={<Layers3 size={16} />}
                    action={
                      <LinkButton
                        onClick={() => go("versions", round.after.id)}
                      >
                        {t.evolution.snapshotBtn(round.after.version)}
                      </LinkButton>
                    }
                  >
                    <div className="pad">
                      {round.after.hypothesis && (
                        <div className="hypothesis">
                          {round.after.hypothesis}
                        </div>
                      )}
                      <div className="tags" style={{ marginBottom: 10 }}>
                        {round.after.paths.map((p) => (
                          <span key={p}>{p}</span>
                        ))}
                      </div>
                      {round.after.prediction && (
                        <div className="hint">
                          {t.evolution.prediction} ·{" "}
                          <code>{round.after.prediction.metric}</code>{" "}
                          {num(round.after.prediction.before)} →{" "}
                          {num(round.after.prediction.after)} ·{" "}
                          <Badge
                            value={
                              round.after.prediction.supported
                                ? t.evolution.supported
                                : t.evolution.unsupported
                            }
                          />
                        </div>
                      )}
                    </div>
                    <DiffViewer text={round.diff} />
                  </Panel>

                  <Panel
                    title={t.evolution.validationAndDecision}
                    icon={<GitCompareArrows size={16} />}
                    action={<Badge value={round.after.decision} />}
                  >
                    <div className="pad">
                      <div className={"decision-banner " + round.after.decision}>
                        <div>
                          <div className="decision-score">
                            v{round.before.version} · {num(round.before.score)} → v
                            {round.after.version} · {num(round.after.score)}
                          </div>
                          <p className="hint" style={{ margin: "4px 0 0" }}>
                            {round.after.decision === "accepted"
                              ? t.evolution.acceptedDesc
                              : round.after.decision === "rejected"
                                ? t.evolution.rejectedDesc(round.before.version)
                                : t.evolution.pendingDesc}
                          </p>
                        </div>
                        <Badge value={round.after.decision} />
                      </div>

                      <div className="facts">
                        {[
                          [
                            t.evolution.fullScorePct,
                            num(round.old_metrics.full_score) +
                              " → " +
                              num(round.new_metrics.full_score),
                          ],
                          [
                            t.evolution.regressedCases,
                            num(round.regression_rate) + "%",
                          ],
                          [
                            t.evolution.tokens,
                            num(round.old_metrics.tokens) +
                              " → " +
                              num(round.new_metrics.tokens),
                          ],
                          [
                            t.evolution.cost,
                            num(round.old_metrics.cost) +
                              " → " +
                              num(round.new_metrics.cost),
                          ],
                        ].map(([k, v]) => (
                          <div key={k}>
                            <small>{k}</small>
                            <strong>{v}</strong>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="tablewrap">
                      <table>
                        <thead>
                          <tr>
                            <th>{t.evolution.benchmarkCase}</th>
                            <th>{t.evolution.reference}</th>
                            <th>{t.evolution.candidate}</th>
                            <th>{t.evolution.delta}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {round.cases.map((c) => (
                            <tr key={c.case}>
                              <td>
                                <details>
                                  <summary>{c.case}</summary>
                                  {round.after.cases
                                    .find((k) => k.case === c.case)
                                    ?.samples.map((s) => (
                                      <p key={s.task} style={{ margin: "6px 0 0" }}>
                                        <LinkButton
                                          onClick={() =>
                                            go("workspace", s.task)
                                          }
                                        >
                                          {t.evolution.sampleBtn(
                                            s.index,
                                            num(s.score),
                                            elapsed(s.duration_ms),
                                          )}
                                        </LinkButton>
                                      </p>
                                    ))}
                                </details>
                              </td>
                              <td className="mono">{c.before}</td>
                              <td className="mono">{c.after}</td>
                              <td
                                className={
                                  "mono " +
                                  (c.delta > 0
                                    ? "gain"
                                    : c.delta < 0
                                      ? "danger"
                                      : "")
                                }
                              >
                                {c.delta > 0 ? "+" : ""}
                                {num(c.delta)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </Panel>
                </>
              )}

              {r.job && (
                <Panel
                  title={t.evolution.lifecycleJournal}
                  icon={<Clock3 size={16} />}
                  action={
                    <span className="unit">
                      {stream.connected
                        ? t.evolution.liveTag
                        : t.evolution.persistedTag}
                    </span>
                  }
                >
                  <div className="eventlog">
                    {lifecycle.length ? (
                      lifecycle.map((e) => (
                        <div className="eventlog-row" key={e.event_id}>
                          <code>{e.timestamp.slice(11, 19)}Z</code>
                          <span>{e.event_type.replaceAll("_", " ")}</span>
                          <small>
                            {String(
                              e.data.case ||
                                e.data.scope ||
                                (e.data.version ? "v" + e.data.version : ""),
                            )}
                          </small>
                        </div>
                      ))
                    ) : (
                      <Empty>{t.evolution.waitingRecordedEvents}</Empty>
                    )}
                  </div>
                </Panel>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}
