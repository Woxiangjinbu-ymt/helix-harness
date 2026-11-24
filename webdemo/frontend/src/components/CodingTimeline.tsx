import { useState } from "react";
import { useI18n } from "../i18n/context";
import type { Messages } from "../i18n/types";
import { api } from "../lib/api";
import { requestId, sessionPath, terminalStates } from "../lib/coding";
import type { Answer, Entry } from "../lib/coding";

type Words = Messages["coding"];
function groupEntries<K>(rows: Entry[], key: (row: Entry) => K) {
  const groups = new Map<K, Entry[]>();
  for (const row of rows) {
    const k = key(row);
    const existing = groups.get(k);
    if (existing) existing.push(row);
    else groups.set(k, [row]);
  }
  return groups;
}
export function stateText(state: string, words: Words) {
  return Object.hasOwn(words, state) ? words[state as keyof Words] : state;
}

function Permission({
  sid,
  entry,
  decision,
  closed,
}: {
  sid: string;
  entry: Entry;
  decision?: Entry;
  closed: boolean;
}) {
  const c = useI18n().t.coding;
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const p = entry.payload;
  async function reply(answer: Answer) {
    if (busy) return;
    setBusy(true);
    setError("");
    const key = "coding.answer." + p.permission_id;
    try {
      await api(
        sessionPath(sid) +
          "/turns/" +
          entry.turn_id +
          "/permissions/" +
          p.permission_id +
          "/reply",
        {
          request_id: requestId(key, JSON.stringify([answer, feedback])),
          decision: answer,
          feedback,
        },
      );
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  return (
    <div className="coding-permission" data-permission={p.permission_id}>
      <strong>{decision || closed ? c.decision : c.waiting_permission}</strong>
      <p>{p.description}</p>
      <small>
        {c.scope} · {p.directory} · <code>{p.scope}</code>
      </small>
      {decision ? (
        <p>
          {decision.payload.decision === "allow_always"
            ? c.requestedRule
            : stateText(decision.payload.decision || "", c)}{" "}
          {decision.payload.feedback}{" "}
          {decision.payload.decision === "deny" && c.resumeAfterDenial}
        </p>
      ) : (
        !closed && (
          <div>
            <input
              aria-label={c.feedback}
              placeholder={c.feedback}
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
            />
            <div className="coding-actions">
              {(["allow", "allow_always", "deny"] as const).map((a) => (
                <button
                  type="button"
                  disabled={busy}
                  key={a}
                  onClick={() => reply(a)}
                >
                  {c[a]}
                </button>
              ))}
            </div>
          </div>
        )
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

function testEvidence(
  command: string,
  output: string,
  exit: number | null | undefined,
  c: Words,
) {
  if (exit == null) return c.unknown;
  if (
    /^\s*python(?:3(?:\.\d+)?)?\s+-m\s+unittest(?:\s+[\w.\/-]+)*\s*$/.test(
      command,
    ) &&
    /^Ran [1-9]\d* tests? in /m.test(output)
  ) {
    if (exit === 0 && /^OK(?:\s|$)/m.test(output)) return c.passed;
    if (exit !== 0 && /^FAILED \(/m.test(output)) return c.testFailed;
  }
  if (/^\s*(?:python3?\s+-m\s+)?pytest(?:\s+[\w.\/-]+)*\s*$/.test(command)) {
    if (exit === 0 && /\b[1-9]\d* passed in /.test(output)) return c.passed;
    if (exit !== 0 && /\b[1-9]\d* failed\b/.test(output)) return c.testFailed;
  }
  return c.unknown;
}

function Output({ text }: { text: string }) {
  const { locale } = useI18n();
  const [page, setPage] = useState(0);
  const size = 16384;
  const pages = Math.ceil(text.length / size);
  const current = Math.min(page, Math.max(0, pages - 1));
  return (
    <>
      <pre>{text.slice(current * size, (current + 1) * size)}</pre>
      {pages > 1 && (
        <nav>
          <button disabled={!current} onClick={() => setPage(current - 1)}>
            {locale === "zh" ? "上一段" : "Previous"}
          </button>
          <span>
            {current + 1} / {pages}
          </span>
          <button
            disabled={current + 1 >= pages}
            onClick={() => setPage(current + 1)}
          >
            {locale === "zh" ? "下一段" : "Next"}
          </button>
        </nav>
      )}
    </>
  );
}
export function CodingTimeline({
  sid,
  entries,
  filter,
  onDiff,
}: {
  sid: string;
  entries: Entry[];
  filter?: "terminal" | "tools";
  onDiff?: () => void;
}) {
  const c = useI18n().t.coding;
  const turns = groupEntries(entries, (e) => e.turn_id);
  return (
    <div className="coding-timeline">
      {Array.from(turns, ([tid, rows]) => {
        const state = rows.filter((e) => e.kind === "state").at(-1)?.payload;
        const closed =
          terminalStates.includes(state?.state || "") ||
          state?.state === "cancelling";
        const model = rows.find((e) => e.payload.model)?.payload;
        const calls = groupEntries(
          rows.filter((e) => e.payload.call_id),
          (e) => e.payload.call_id,
        );
        const messages = groupEntries(
          rows.filter((e) => e.payload.message_id),
          (e) => e.kind + e.payload.message_id,
        );
        return (
          <section key={tid} data-turn={tid}>
            {!filter && (
              <header className="coding-meta">
                <span>
                  {model?.provider} {model?.model}
                </span>
                <span data-state={state?.state}>
                  {stateText(state?.state || "", c)}
                </span>
              </header>
            )}
            {rows.map((entry) => {
              const p = entry.payload;
              if (!filter && entry.kind === "user")
                return (
                  <div className="coding-user" key={entry.seq}>
                    {p.text}
                  </div>
                );
              if (
                !filter &&
                p.message_id &&
                (entry.kind === "text" || entry.kind === "thinking")
              ) {
                const parts = messages.get(entry.kind + p.message_id)!;
                if (parts[0] !== entry) return null;
                const text = parts.map((r) => r.payload.text).join("");
                return entry.kind === "thinking" ? (
                  <details key={entry.seq}>
                    <summary>{c.thinking}</summary>
                    <pre>{text}</pre>
                  </details>
                ) : (
                  <div key={entry.seq} className="coding-text">
                    {text}
                  </div>
                );
              }
              if (
                entry.kind === "tool_call" &&
                (filter !== "terminal" || p.tool === "Bash")
              ) {
                const related = calls.get(p.call_id) || [];
                const unknown = related.some(
                  (e) => e.kind === "unknown_result",
                );
                const result = related.find(
                  (e) => e.kind === "tool_result",
                )?.payload;
                const command = related.find(
                  (e) => e.kind === "process_started",
                )?.payload;
                const end = related.find(
                  (e) => e.kind === "process_exited",
                )?.payload;
                const output = related
                  .filter((e) => e.kind === "output")
                  .map(
                    (e) =>
                      e.payload.text ||
                      (e.payload.truncated ? c.truncated : ""),
                  )
                  .join("");
                const permissions = related.filter(
                  (e) => e.kind === "permission",
                );
                return (
                  <article
                    className="coding-call"
                    key={entry.seq}
                    id={filter ? undefined : "call-" + p.call_id}
                    data-call={p.call_id}
                  >
                    <details open={filter ? true : undefined}>
                      <summary>
                        <b>
                          {p.hook_id ? "Hook · " + p.hook_id + " · " : ""}
                          {p.tool}
                        </b>{" "}
                        ·{" "}
                        {result
                          ? result.is_error
                            ? c.failed
                            : c.completed
                          : unknown
                            ? c.unknownAction
                            : state?.state === "cancelled"
                              ? c.cancelled
                              : c.pending}
                      </summary>
                      <small>{c.arguments}</small>
                      <pre>{JSON.stringify(p.args, null, 2)}</pre>
                      {end &&
                        end.exit_code != null &&
                        end.exit_code !== 0 &&
                        result?.is_error && (
                          <small>
                            {c.commandError} · {c.reviewCommand}
                          </small>
                        )}
                      {command && (
                        <small>
                          {command.cwd} $ {command.command}
                        </small>
                      )}
                      {related
                        .filter((e) => e.kind === "write_conflict")
                        .map((e) => (
                          <p key={e.seq}>
                            {c.conflict}{" "}
                            <a href={"#coding/" + e.payload.holder_session_id}>
                              {e.payload.holder_session_id}
                            </a>
                          </p>
                        ))}
                      <Output text={output || result?.output || ""} />
                      {(result?.truncated ||
                        related.some(
                          (e) => e.kind === "output" && e.payload.truncated,
                        )) && <small>{c.truncated}</small>}
                      {end && (
                        <small>
                          {c.exit} {end.exit_code ?? "?"} ·{" "}
                          {testEvidence(
                            command?.command || "",
                            output,
                            end.exit_code,
                            c,
                          )}
                        </small>
                      )}
                      {related.some(
                        (e) => e.kind === "action_ended" && e.payload.scope,
                      ) && <small>{c.ruleSaved}</small>}
                    </details>
                    {related
                      .filter((e) => e.payload.rule_error)
                      .map((e) => (
                        <p role="alert" key={e.seq}>
                          {c.ruleError} · {e.payload.rule_error}
                        </p>
                      ))}
                    {unknown && onDiff && <button onClick={onDiff}>{c.diff}</button>}
                    {permissions.map((e) => (
                      <Permission
                        key={e.seq}
                        sid={sid}
                        entry={e}
                        closed={closed}
                        decision={related.find(
                          (d) =>
                            d.kind === "decision" &&
                            d.payload.permission_id === e.payload.permission_id,
                        )}
                      />
                    ))}
                  </article>
                );
              }
              if (
                !filter &&
                (entry.kind === "error" || entry.kind === "compact")
              )
                return <p key={entry.seq}>{p.text}</p>;
              if (!filter && entry.kind === "extension")
                return (
                  <p key={entry.seq}>
                    {p.extension} · {p.name} · {p.status} {p.tools?.join(", ")}{" "}
                    {p.error}
                  </p>
                );
              if (!filter && entry.kind === "cleaning")
                return <small key={entry.seq}>{p.reason}</small>;
              if (!filter && entry.kind === "summary")
                return (
                  <details key={entry.seq}>
                    <summary>{c.summary}</summary>
                    {p.changes?.map((change) => (
                      <button
                        key={change.path}
                        onClick={() =>
                          document
                            .getElementById("call-" + change.call_id)
                            ?.scrollIntoView()
                        }
                      >
                        {change.path} {change.uncertain ? "· " + c.mixed : ""}
                      </button>
                    ))}
                    {rows
                      .filter(
                        (e) =>
                          e.kind === "tool_result" &&
                          (e.payload.is_error || e.payload.tool === "Bash"),
                      )
                      .map((e) => {
                        const r = calls.get(e.payload.call_id) || [];
                        const cmd =
                          r.find((x) => x.kind === "process_started")?.payload
                            .command || "";
                        const end = r.find(
                          (x) => x.kind === "process_exited",
                        )?.payload;
                        return (
                          <button
                            key={e.seq}
                            onClick={() =>
                              document
                                .getElementById("call-" + e.payload.call_id)
                                ?.scrollIntoView()
                            }
                          >
                            {e.payload.tool} ·{" "}
                            {e.payload.tool === "Bash"
                              ? testEvidence(
                                  cmd,
                                  e.payload.output || "",
                                  end?.exit_code,
                                  c,
                                )
                              : c.failed}
                          </button>
                        );
                      })}
                  </details>
                );
              return null;
            })}
            {!filter && state?.reason && (
              <p role="status">
                {state.error_type === "storage"
                  ? c.storageFault
                  : state.error_type === "model"
                    ? c.modelError + " · " + model?.model + " · "
                    : ""}
                {state.reason}{" "}
                {state.error_type === "model" && (
                  <a href={"#coding-settings/" + sid}>{c.checkSettings}</a>
                )}
              </p>
            )}
          </section>
        );
      })}
    </div>
  );
}
