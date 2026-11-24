import { useState } from "react";
import { FileCode2, GitBranch, Layers3, ShieldCheck } from "lucide-react";
import { useI18n } from "../i18n/context";
import { go, useData } from "../lib/api";
import { formatDate, num } from "../lib/format";
import { CodeViewer } from "../components/CodeViewer";
import { DiffViewer } from "../components/DiffViewer";
import {
  Badge,
  CopyButton,
  Empty,
  LinkButton,
  Notice,
  Panel,
} from "../components/ui";
import { groupLineages, LineageSelect } from "./Evaluations";
import type { Version } from "../types";

export function Versions({ id }: { id?: string }) {
  const { t, locale } = useI18n();
  const versions = useData<Version[]>("versions");
  const [tab, setTab] = useState<"Changes" | "Files">("Changes");

  const all = versions.data || [];
  const lines = groupLineages(all);
  const initial =
    all.find((v) => v.id === id) ||
    all.find((v) => v.active && v.id.startsWith(lines[0] + "~"));

  const detail = useData<
    Version & {
      files: Record<string, string>;
      diff: string;
      manual_rollback: boolean;
    }
  >(initial ? "versions/" + initial.id : null);

  if (!versions.data) return <Notice error={versions.error} />;

  const v = detail.data;
  const line = initial ? initial.study + "~" + initial.base : "";
  const vs = all
    .filter((x) => x.id.startsWith(line + "~"))
    .sort((a, b) => a.version - b.version);

  function selectLine(key: string) {
    const entry = all.find((x) => x.id.startsWith(key + "~") && x.active);
    if (entry) go("versions", entry.id);
  }

  return (
    <>
      <div className="page-title">
        <div>
          <h1>{t.versions.title}</h1>
          <p>{t.versions.subtitle}</p>
        </div>
        <LineageSelect
          lines={lines}
          selected={line}
          vs={all}
          change={selectLine}
          ariaLabel={t.evaluations.experimentLineage}
        />
      </div>

      <div className="researchgrid">
        <Panel title={t.versions.lineageTitle} icon={<GitBranch size={16} />}>
          <div className="lineage">
            {vs.map((x) => {
              const dotClass = x.active
                ? "active"
                : x.decision === "rejected"
                  ? "rejected"
                  : x.decision === "accepted"
                    ? "accepted"
                    : "";
              const indent = x.decision === "rejected" ? 14 : 0;
              return (
                <button
                  type="button"
                  key={x.id}
                  className={"listselect " + (x.id === initial?.id ? "on" : "")}
                  style={{ paddingLeft: 14 + indent }}
                  onClick={() => go("versions", x.id)}
                >
                  <div className="lineage-item">
                    <span className="lineage-branch">
                      <span className={"lineage-dot " + dotClass} />
                    </span>
                    <span>
                      <b>
                        v{x.version}
                        {x.active ? " · " + t.versions.activeTag : ""}
                      </b>
                      <small>
                        {x.parent
                          ? t.versions.parentPrefix(x.parent)
                          : t.versions.initialSnapshot}{" "}
                        · {num(x.score)} {t.dashboard.pts}
                      </small>
                    </span>
                  </div>
                  <Badge value={x.decision} />
                </button>
              );
            })}
            {!vs.length && <Empty>{t.versions.noSnapshots}</Empty>}
          </div>
        </Panel>

        <div className="stack">
          {v ? (
            <>
              <Panel
                title={t.versions.harnessPrefix + v.version}
                icon={<Layers3 size={16} />}
                action={
                  <div className="row">
                    <Badge value={v.mode} />
                    <Badge value={v.active ? "active" : v.decision} />
                  </div>
                }
              >
                <div className="facts pad">
                  {[
                    [t.versions.parent, v.parent ? "v" + v.parent : "—"],
                    [t.versions.benchmarkScore, num(v.score)],
                    [t.versions.recorded, formatDate(v.date, locale)],
                    [t.versions.model, v.model],
                  ].map(([k, val]) => (
                    <div key={k}>
                      <small>{k}</small>
                      <strong>{val}</strong>
                    </div>
                  ))}
                </div>

                <div
                  className="pad row"
                  style={{
                    paddingTop: 0,
                    justifyContent: "space-between",
                    flexWrap: "wrap",
                  }}
                >
                  <span className="hint wrap">
                    {t.versions.snapshotSha} · <code>{v.digest}</code>
                  </span>
                  <CopyButton text={v.digest} />
                </div>

                {v.hypothesis && (
                  <div className="pad" style={{ paddingTop: 0 }}>
                    <div className="hypothesis" style={{ marginBottom: 0 }}>
                      {v.hypothesis}
                    </div>
                  </div>
                )}

                <div className="pad btns" style={{ borderTop: "1px solid var(--border-subtle)" }}>
                  {v.session && (
                    <LinkButton
                      onClick={() =>
                        go(
                          "evolution",
                          /^[0-9a-f]{32}$/.test(v.study)
                            ? v.session!
                            : v.study + "~" + v.session,
                        )
                      }
                    >
                      {t.versions.evolutionReport}
                    </LinkButton>
                  )}
                  <span className="hint">
                    {v.decision === "rejected"
                      ? t.versions.rejectedReason(v.parent)
                      : v.decision === "accepted"
                        ? t.versions.acceptedReason
                        : t.versions.frozenBaseline}
                  </span>
                </div>
              </Panel>

              <Panel
                title={t.versions.snapshotFiles}
                icon={<FileCode2 size={16} />}
                action={
                  <div className="tabs">
                    {(
                      [
                        ["Changes", t.versions.tabChanges],
                        ["Files", t.versions.tabFiles],
                      ] as const
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
                }
              >
                {tab === "Changes" ? (
                  <>
                    <div className="pad hint" style={{ paddingBottom: 10 }}>
                      {v.paths.length
                        ? v.paths.join(" · ")
                        : t.versions.initialSnapshot}
                    </div>
                    <DiffViewer text={v.diff} />
                  </>
                ) : (
                  <CodeViewer
                    files={v.files}
                    ariaLabel={t.workspace.artifactFileAria}
                  />
                )}
              </Panel>

              <Panel
                title={t.versions.governanceTitle}
                icon={<ShieldCheck size={16} />}
              >
                <div className="pad hint">{t.versions.governanceBody}</div>
              </Panel>
            </>
          ) : initial ? (
            <Notice error={detail.error} />
          ) : (
            <Empty>{t.versions.noHarnessSelected}</Empty>
          )}
        </div>
      </div>
    </>
  );
}
