import { useEffect, useRef, useState } from "react";
import {
  Bot,
  CheckCircle2,
  Flag,
  Gauge,
  ShieldAlert,
  ShieldCheck,
  Terminal,
  Wrench,
  XCircle,
} from "lucide-react";
import { useI18n } from "../i18n/context";
import type { Trace } from "../types";
import { Badge, Empty } from "./ui";

type FilterKind = "all" | "tools" | "model" | "permissions";

export function TraceTimeline({
  events,
  showFilters = false,
  live = false,
}: {
  events: Trace[];
  showFilters?: boolean;
  live?: boolean;
}) {
  const { t } = useI18n();
  const [filter, setFilter] = useState<FilterKind>("all");
  const bottomRef = useRef<HTMLDivElement>(null);

  const calls = new Map(
    events.filter((e) => e.type === "tool_use").map((e) => [e.tool_id, e.tool]),
  );

  const filtered = events.filter((e) => {
    if (filter === "all") return true;
    if (filter === "tools")
      return e.type === "tool_use" || e.type === "tool_result";
    if (filter === "model")
      return e.type === "model_request" || e.type === "usage";
    if (filter === "permissions") return e.type === "permission";
    return true;
  });

  useEffect(() => {
    if (live && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [events.length, live]);

  const iconFor = (e: Trace) => {
    switch (e.type) {
      case "model_request":
        return <Bot size={13} />;
      case "tool_use":
        return <Wrench size={13} />;
      case "tool_result":
        return e.is_error ? <XCircle size={13} /> : <CheckCircle2 size={13} />;
      case "permission":
        return e.decision === "deny" ? (
          <ShieldAlert size={13} />
        ) : (
          <ShieldCheck size={13} />
        );
      case "usage":
        return <Gauge size={13} />;
      case "terminal":
        return <Flag size={13} />;
      default:
        return <Terminal size={13} />;
    }
  };

  return (
    <>
      {showFilters && (
        <div className="trace-toolbar">
          <div className="tabs">
            {(
              [
                ["all", t.workspace.filterAll],
                ["tools", t.workspace.filterTools],
                ["model", t.workspace.filterModel],
                ["permissions", t.workspace.filterPermissions],
              ] as [FilterKind, string][]
            ).map(([k, label]) => (
              <button
                type="button"
                key={k}
                className={filter === k ? "on" : ""}
                onClick={() => setFilter(k)}
              >
                {label}
              </button>
            ))}
          </div>
          {filter !== "all" && (
            <span className="unit">{t.workspace.eventsCount(filtered.length)}</span>
          )}
        </div>
      )}
      {filtered.length ? (
        <div className="tracelist">
          {filtered.map((e) => {
            const toolName =
              e.tool || (e.type === "tool_result" ? calls.get(e.tool_id) : undefined);
            const title =
              t.workspace.traceTypes[e.type] || e.type.replaceAll("_", " ");
            const details = [
              e.model_id,
              e.status,
              e.is_error === true
                ? t.workspace.error
                : e.is_error === false
                  ? t.workspace.ok
                  : null,
              e.turn ? t.workspace.turnSuffix(e.turn) : null,
              e.input_tokens != null
                ? t.workspace.tokensIO(e.input_tokens, e.output_tokens || 0)
                : null,
            ]
              .filter(Boolean)
              .join(" · ");

            return (
              <div className={"trace t-" + e.type} key={e.session_id + "-" + e.seq}>
                <code className="trace-seq">{String(e.seq).padStart(2, "0")}</code>
                <span className="trace-icon">{iconFor(e)}</span>
                <div className="trace-body">
                  <div className="trace-title-row">
                    <b>{title}</b>
                    {toolName && <span className="trace-tool-pill">{toolName}</span>}
                    {e.file && <span className="trace-file-pill">{e.file}</span>}
                    {e.decision && <Badge value={e.decision} />}
                  </div>
                  {details && <small>{details}</small>}
                </div>
                <span className="trace-meta">
                  {e.elapsed_ms != null ? e.elapsed_ms + " ms" : ""}
                </span>
              </div>
            );
          })}
          <div ref={bottomRef} />
        </div>
      ) : (
        <Empty>{t.workspace.noToolCalls}</Empty>
      )}
    </>
  );
}
