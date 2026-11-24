import { useState } from "react";
import {
  Activity,
  Box,
  ChartNoAxesCombined,
  ChevronRight,
  FlaskConical,
  GitBranch,
  GitPullRequest,
  Play,
  Plus,
  Terminal,
} from "lucide-react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useI18n } from "../i18n/context";
import { useTheme } from "../theme/context";
import { go, useData } from "../lib/api";
import { formatDate, num, short } from "../lib/format";
import { Badge, Empty, LinkButton, Notice, Panel } from "../components/ui";
import type { Overview } from "../types";

export function Dashboard() {
  const { t, locale } = useI18n();
  const { chart } = useTheme();
  const { data, error } = useData<Overview>("dashboard/overview");
  const [mode, setMode] = useState("All sources");
  const [selected, setSelected] = useState("");

  if (!data) return <Notice error={error} />;

  const tasks = data.tasks.filter(
    (item) => mode === "All sources" || item.mode === mode,
  );
  const runs = data.evolutions.filter(
    (item) => mode === "All sources" || item.mode === mode,
  );
  const versions = data.versions.filter(
    (item) => mode === "All sources" || item.mode === mode,
  );

  const lines = [...new Set(versions.map((v) => v.study + "~" + v.base))].sort(
    (a, b) =>
      versions.filter((v) => v.id.startsWith(b + "~")).length -
      versions.filter((v) => v.id.startsWith(a + "~")).length,
  );
  const line = lines.includes(selected) ? selected : lines[0];
  const vs = versions
    .filter((v) => v.id.startsWith(line + "~"))
    .sort((a, b) => a.version - b.version);

  const active = vs.find((v) => v.active);
  const base = vs[0];
  const scored = tasks.filter((item) => item.score != null);
  const successes = scored.filter((item) => item.score === 100);
  const usages = tasks.filter(
    (item) =>
      item.usage && item.usage.input_tokens + item.usage.output_tokens > 0,
  );

  let head: number | null = null;
  const points = vs.map((v) => {
    if (v.decision === "accepted" || v.decision === "baseline") head = v.score;
    return {
      name: "v" + v.version,
      score: v.score,
      reference: head,
      decision: v.decision,
    };
  });

  const delta =
    active?.score != null && base?.score != null
      ? active.score - base.score
      : null;

  return (
    <>
      <div className="page-title">
        <div>
          <h1>{t.dashboard.title}</h1>
          <p>{t.dashboard.subtitle}</p>
        </div>
        <div className="page-actions">
          <select
            aria-label={t.dashboard.filterSourcesAria}
            value={mode}
            onChange={(e) => setMode(e.target.value)}
          >
            <option value="All sources">{t.controls.allSources}</option>
            <option value="Real">{t.controls.real}</option>
            <option value="Mock">{t.controls.mock}</option>
          </select>
          <button
            type="button"
            onClick={() => go("evolution")}
          >
            <Play size={13} />
            {t.dashboard.startEvolution}
          </button>
          <button
            type="button"
            className="primary"
            onClick={() => go("workspace")}
          >
            <Plus size={14} />
            {t.dashboard.newCodingTask}
          </button>
        </div>
      </div>

      <div className="kpis">
        {[
          {
            icon: Terminal,
            label: t.dashboard.kpis.codingRuns,
            value: num(tasks.length),
            sub: t.dashboard.kpis.codingRunsSub,
            tone: "mint",
          },
          {
            icon: Box,
            label: t.dashboard.kpis.benchmarkTasks,
            value: num(data.benchmark_tasks),
            sub: data.benchmark,
            tone: "blue",
          },
          {
            icon: GitBranch,
            label: t.dashboard.kpis.activeHarness,
            value: active ? "v" + active.version : "—",
            sub: t.dashboard.kpis.activeHarnessSub,
            tone: "violet",
          },
          {
            icon: FlaskConical,
            label: t.dashboard.kpis.evolutionRuns,
            value: num(runs.length),
            sub: t.dashboard.kpis.evolutionRunsSub,
            tone: "amber",
          },
        ].map(({ icon: Icon, label, value, sub, tone }) => (
          <section className="kpi" key={label}>
            <div className="kpi-head">
              <span>{label}</span>
              <i className={"iconbox " + tone}>
                <Icon size={16} />
              </i>
            </div>
            <strong>{value}</strong>
            <small>{sub}</small>
          </section>
        ))}
      </div>

      <div className="dashgrid">
        <Panel
          title={t.dashboard.performanceTitle}
          icon={<ChartNoAxesCombined size={16} />}
          action={<span className="unit">{t.dashboard.scoreUnit}</span>}
        >
          <div className="chartinfo">
            <div className="chartinfo-metric">
              <strong>{num(active?.score)}</strong>
              {delta != null && (
                <span className={delta >= 0 ? "positive" : "negative"}>
                  {(delta >= 0 ? "+" : "") + num(delta) + " " + t.dashboard.pts}
                </span>
              )}
              <small>{t.dashboard.activeVsBaseline}</small>
            </div>
            <select
              aria-label={t.dashboard.chartLineageAria}
              value={line || ""}
              onChange={(e) => setSelected(e.target.value)}
            >
              {lines.map((l) => (
                <option value={l} key={l}>
                  {versions.find((v) => v.id.startsWith(l + "~"))?.mode} ·{" "}
                  {l.split("~")[1]}
                </option>
              ))}
            </select>
          </div>
          {vs.length ? (
            <div className="chart">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart
                  data={points}
                  margin={{ top: 10, right: 16, bottom: 0, left: -18 }}
                >
                  <defs>
                    <linearGradient id="helixScoreFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={chart.score} stopOpacity={0.22} />
                      <stop offset="100%" stopColor={chart.score} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    stroke={chart.grid}
                    vertical={false}
                    strokeDasharray="3 5"
                  />
                  <XAxis
                    dataKey="name"
                    tickLine={false}
                    axisLine={false}
                    tick={{ fill: chart.axis, fontSize: 11 }}
                  />
                  <YAxis
                    domain={[0, 100]}
                    ticks={[0, 25, 50, 75, 100]}
                    tickLine={false}
                    axisLine={false}
                    tick={{ fill: chart.axis, fontSize: 11 }}
                  />
                  <Tooltip
                    contentStyle={{
                      background: chart.tooltipBg,
                      border: `1px solid ${chart.tooltipBorder}`,
                      borderRadius: 8,
                      color: chart.tooltipText,
                      fontSize: 12,
                    }}
                  />
                  <Area
                    name={t.dashboard.candidateScore}
                    type="linear"
                    dataKey="score"
                    stroke={chart.score}
                    strokeWidth={2.5}
                    fill="url(#helixScoreFill)"
                    dot={{
                      r: 4,
                      fill: chart.score,
                      stroke: chart.scoreDotStroke,
                      strokeWidth: 2,
                    }}
                    isAnimationActive={false}
                  />
                  <Line
                    name={t.dashboard.activeReference}
                    type="stepAfter"
                    dataKey="reference"
                    stroke={chart.reference}
                    strokeWidth={1.75}
                    strokeDasharray="5 5"
                    dot={false}
                    isAnimationActive={false}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <Empty>{t.dashboard.noVersionScores}</Empty>
          )}
          <div className="legend">
            <span>
              <i />
              {t.dashboard.candidateScore}
            </span>
            <span>
              <i className="purple" />
              {t.dashboard.activeReference}
            </span>
          </div>
        </Panel>

        <Panel title={t.dashboard.summaryTitle} icon={<Activity size={16} />}>
          <div className="summary">
            <div className="summary-row">
              <span>{t.dashboard.benchmark}</span>
              <b className="mono">{data.benchmark}</b>
            </div>
            <div className="summary-row">
              <span>{t.dashboard.fullScoreRuns}</span>
              <b>
                {successes.length}{" "}
                <em>{t.dashboard.scoredSuffix(scored.length)}</em>
              </b>
            </div>
            <div className="meter">
              <i
                style={{
                  width:
                    (scored.length
                      ? (successes.length / scored.length) * 100
                      : 0) + "%",
                }}
              />
            </div>
            <div className="summary-row">
              <span>{t.dashboard.successRate}</span>
              <b>
                {scored.length
                  ? num((successes.length / scored.length) * 100) + "%"
                  : "—"}
              </b>
            </div>
            <div className="summary-row">
              <span>{t.dashboard.reportedTokens}</span>
              <b className="mono">
                {usages.length
                  ? num(
                      usages.reduce(
                        (n, item) =>
                          n +
                          item.usage!.input_tokens +
                          item.usage!.output_tokens,
                        0,
                      ),
                    )
                  : "—"}
              </b>
            </div>
            <small className="hint">
              {t.dashboard.usageCoverage(usages.length, tasks.length)}
            </small>
          </div>
          <div className="footaction">
            <LinkButton onClick={() => go("evaluations")}>
              {t.dashboard.openEvaluations}
            </LinkButton>
          </div>
        </Panel>
      </div>

      <div className="lowergrid">
        <Panel
          title={t.dashboard.recentTasksTitle}
          icon={<Terminal size={16} />}
          action={
            <LinkButton onClick={() => go("workspace")}>
              {t.dashboard.viewAll}
            </LinkButton>
          }
        >
          {tasks.slice(0, 5).map((item) => (
            <button
              type="button"
              className="record"
              key={item.id}
              onClick={() => go("workspace", item.job || item.id)}
            >
              <i className="rowicon">
                <Terminal size={15} />
              </i>
              <span className="record-body">
                <b>{item.case}</b>
                <small>
                  {item.model || item.status} · {formatDate(item.date, locale)}
                </small>
              </span>
              <Badge value={item.mode} />
              <strong className="record-score">{num(item.score)}</strong>
              <ChevronRight size={14} />
            </button>
          ))}
          {!tasks.length && <Empty>{t.dashboard.noCodingRuns}</Empty>}
        </Panel>

        <Panel
          title={t.dashboard.recentEvolutionTitle}
          icon={<GitPullRequest size={16} />}
          action={
            <LinkButton onClick={() => go("evolution")}>
              {t.dashboard.viewAll}
            </LinkButton>
          }
        >
          {runs.slice(0, 4).map((r) => (
            <button
              type="button"
              className="record"
              key={r.id}
              onClick={() => go("evolution", r.id)}
            >
              <i className="rowicon purple">
                <GitBranch size={15} />
              </i>
              <span className="record-body">
                <b>{short(r.session)}</b>
                <small>
                  {r.mode} · {r.reason?.replaceAll("_", " ") || r.status}
                </small>
              </span>
              <Badge value={r.status} />
              <ChevronRight size={14} />
            </button>
          ))}
          {!runs.length && <Empty>{t.dashboard.noEvolutionRuns}</Empty>}
        </Panel>
      </div>
    </>
  );
}
