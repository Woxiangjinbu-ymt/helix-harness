import { useState } from "react";
import { BookOpen, FlaskConical, GitCompareArrows } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useI18n } from "../i18n/context";
import { useTheme } from "../theme/context";
import { go, useData } from "../lib/api";
import { elapsed, num } from "../lib/format";
import { DiffViewer } from "../components/DiffViewer";
import { Badge, Empty, LinkButton, Notice, Panel } from "../components/ui";
import type { Compare, Metrics, Repo, Task, Version } from "../types";

export function groupLineages(vs: Version[]) {
  return [...new Set(vs.map((v) => v.study + "~" + v.base))].sort(
    (a, b) =>
      vs.filter((v) => v.id.startsWith(b + "~")).length -
      vs.filter((v) => v.id.startsWith(a + "~")).length,
  );
}

export function LineageSelect({
  lines,
  selected,
  change,
  vs,
  ariaLabel,
}: {
  lines: string[];
  selected: string;
  change: (s: string) => void;
  vs: Version[];
  ariaLabel: string;
}) {
  return (
    <select
      aria-label={ariaLabel}
      value={selected}
      onChange={(e) => change(e.target.value)}
    >
      {lines.map((l) => (
        <option key={l} value={l}>
          {vs.find((v) => v.id.startsWith(l + "~"))?.mode} · {l.split("~")[1]}
        </option>
      ))}
    </select>
  );
}

export function Evaluations() {
  const { t } = useI18n();
  const { chart } = useTheme();
  const versions = useData<Version[]>("versions");
  const benchmarks =
    useData<{ id: string; split: string; cases: Repo[] }[]>("benchmarks");
  const records = useData<Task[]>("evaluations");

  const [chosen, choose] = useState("");
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");
  const [filter, setFilter] = useState("All sources");

  const all = versions.data || [];
  const lines = groupLineages(all);
  const line = lines.includes(chosen) ? chosen : lines[0];
  const vs = all
    .filter((v) => v.id.startsWith(line + "~") && v.score != null)
    .sort((a, b) => a.version - b.version);

  const a = vs.find((v) => v.id === left) || vs[0];
  const b =
    vs.find((v) => v.id === right) || vs.find((v) => v.active) || vs.at(-1);

  const compare = useData<Compare>(
    a && b
      ? "evaluations/compare?" +
          new URLSearchParams({ left: a.id, right: b.id })
      : null,
  );
  const result = compare.data;
  const err =
    versions.error || benchmarks.error || records.error || compare.error;

  if (!versions.data || !benchmarks.data) return <Notice error={err} />;

  const metrics: [string, keyof Metrics, (v: number | null) => string][] = [
    [
      t.evaluations.runCompletion,
      "completion",
      (v) => (v == null ? "—" : num(v) + "%"),
    ],
    [
      t.evaluations.fullScoreRuns,
      "full_score",
      (v) => (v == null ? "—" : num(v) + "%"),
    ],
    [
      t.evaluations.testPassRate,
      "test_pass",
      (v) => (v == null ? "—" : num(v) + "%"),
    ],
    [t.evaluations.reportedTokens, "tokens", num],
    [t.evaluations.meanDuration, "duration_ms", elapsed],
    [t.evaluations.meanCost, "cost", num],
  ];

  const filteredRecords = (records.data || []).filter(
    (item) => filter === "All sources" || item.mode === filter,
  );

  return (
    <>
      <div className="page-title">
        <div>
          <h1>{t.evaluations.title}</h1>
          <p>{t.evaluations.subtitle}</p>
        </div>
      </div>

      {err && <Notice error={err} />}

      {benchmarks.data.map((m) => (
        <Panel
          key={m.id}
          title={m.id}
          icon={<BookOpen size={16} />}
          action={
            <span className="unit">{t.evaluations.tasksUnit(m.cases.length)}</span>
          }
        >
          <div className="pad benchmarkline">
            <div>
              <b>{t.evaluations.repoBenchmark}</b>
              <p className="hint">{t.evaluations.splitNote}</p>
            </div>
            <div className="tags">
              {m.cases.map((c) => (
                <span key={c.id}>{c.id}</span>
              ))}
            </div>
          </div>
        </Panel>
      ))}

      <Panel
        title={t.evaluations.versionComparison}
        icon={<GitCompareArrows size={16} />}
        cls="spaced"
      >
        <div className="formrow fields pad">
          <label>
            {t.evaluations.experimentLineage}
            <LineageSelect
              lines={lines}
              selected={line || ""}
              vs={all}
              change={choose}
              ariaLabel={t.evaluations.experimentLineage}
            />
          </label>
          <label>
            {t.evaluations.reference}
            <select
              aria-label={t.evaluations.referenceVersionAria}
              value={a?.id || ""}
              onChange={(e) => setLeft(e.target.value)}
            >
              {vs.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version} · {v.decision}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t.evaluations.candidate}
            <select
              aria-label={t.evaluations.candidateVersionAria}
              value={b?.id || ""}
              onChange={(e) => setRight(e.target.value)}
            >
              {vs.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version} · {v.decision}
                </option>
              ))}
            </select>
          </label>
        </div>

        {result ? (
          <>
            {result.cases.length > 0 && (
              <div
                style={{
                  borderTop: "1px solid var(--border-subtle)",
                  paddingTop: 12,
                }}
              >
                <div className="pad" style={{ paddingBottom: 0, paddingTop: 4 }}>
                  <span className="unit">{t.evaluations.perCaseChartTitle}</span>
                </div>
                <div className="chart-sm">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={result.cases}
                      margin={{ top: 10, right: 16, bottom: 0, left: -16 }}
                    >
                      <CartesianGrid
                        stroke={chart.grid}
                        vertical={false}
                        strokeDasharray="3 5"
                      />
                      <XAxis
                        dataKey="case"
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
                      <Legend wrapperStyle={{ fontSize: 12 }} />
                      <Bar
                        dataKey="before"
                        name={t.evaluations.referenceCol(result.before.version)}
                        fill={chart.beforeBar}
                        radius={[4, 4, 0, 0]}
                        isAnimationActive={false}
                      />
                      <Bar
                        dataKey="after"
                        name={t.evaluations.candidateCol(result.after.version)}
                        fill={chart.afterBar}
                        radius={[4, 4, 0, 0]}
                        isAnimationActive={false}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}

            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.evaluations.measure}</th>
                    <th>{t.evaluations.referenceCol(result.before.version)}</th>
                    <th>{t.evaluations.candidateCol(result.after.version)}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>{t.evaluations.benchmarkScore}</td>
                    <td className="mono">{num(result.before.score)}</td>
                    <td className="mono">{num(result.after.score)}</td>
                  </tr>
                  {metrics.map(([label, key, fmt]) => (
                    <tr key={key}>
                      <td>{label}</td>
                      <td className="mono">{fmt(result.old_metrics[key])}</td>
                      <td className="mono">{fmt(result.new_metrics[key])}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="pad hint">
              {t.evaluations.regressedNote(num(result.regression_rate))}
            </div>

            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.evaluations.task}</th>
                    <th>{t.evaluations.reference}</th>
                    <th>{t.evaluations.candidate}</th>
                    <th>{t.evaluations.change}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.cases.map((c) => (
                    <tr key={c.case}>
                      <td className="mono">{c.case}</td>
                      <td className="mono">{num(c.before)}</td>
                      <td className="mono">{num(c.after)}</td>
                      <td
                        className={
                          "mono " +
                          (c.delta > 0 ? "gain" : c.delta < 0 ? "danger" : "")
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

            <details className="pad">
              <summary>{t.evaluations.harnessFileChanges}</summary>
              <div style={{ marginTop: 12 }}>
                <DiffViewer text={result.diff} />
              </div>
            </details>
          </>
        ) : !a ? (
          <Empty>{t.evaluations.noCompletedVersions}</Empty>
        ) : (
          <Notice error={compare.error} />
        )}
      </Panel>

      <Panel
        title={t.evaluations.receiptsTitle}
        icon={<FlaskConical size={16} />}
        cls="spaced"
        action={
          <select
            aria-label={t.evaluations.evaluationSourceAria}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="All sources">{t.controls.allSources}</option>
            <option value="Real">{t.controls.real}</option>
            <option value="Mock">{t.controls.mock}</option>
          </select>
        }
      >
        {filteredRecords.length ? (
          <div className="tablewrap tall">
            <table>
              <thead>
                <tr>
                  <th>{t.evaluations.caseCol}</th>
                  <th>{t.evaluations.modelCol}</th>
                  <th>{t.evaluations.harnessCol}</th>
                  <th>{t.evaluations.sourceCol}</th>
                  <th>{t.evaluations.scoreCol}</th>
                  <th>{t.evaluations.durationCol}</th>
                </tr>
              </thead>
              <tbody>
                {filteredRecords.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <LinkButton
                        onClick={() => go("workspace", item.job || item.id)}
                      >
                        {item.case}
                      </LinkButton>
                    </td>
                    <td className="mono">{item.model}</td>
                    <td className="mono">v{item.version}</td>
                    <td>
                      <Badge value={item.mode} />
                    </td>
                    <td className="mono">{num(item.score)}</td>
                    <td className="mono">{elapsed(item.duration_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>{t.evaluations.noReceipts}</Empty>
        )}
      </Panel>
    </>
  );
}
