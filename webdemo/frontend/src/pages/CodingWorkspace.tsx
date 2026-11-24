import { useLayoutEffect, useRef, useState } from "react";
import {
  ArrowUp,
  ChevronRight,
  Code2,
  Files,
  FlaskConical,
  GitPullRequest,
  Globe,
  Moon,
  Plus,
  Settings,
  Sun,
  Terminal,
  Wrench,
  X,
} from "lucide-react";
import { useI18n } from "../i18n/context";
import { useTheme } from "../theme/context";
import { api, go, useData } from "../lib/api";
import {
  requestId,
  sessionPath,
  terminalStates,
  useSession,
} from "../lib/coding";
import type { Session } from "../lib/coding";
import { CodeViewer } from "../components/CodeViewer";
import { DiffViewer } from "../components/DiffViewer";
import { CodingTimeline } from "../components/CodingTimeline";

type Panel = "files" | "diff" | "terminal" | "tools";
type Change = {
  path: string;
  status: string;
  staged?: string;
  working?: string;
};
const panels = {
  files: Files,
  diff: GitPullRequest,
  terminal: Terminal,
  tools: Wrench,
};

function FilesPanel({
  sid,
  diff,
  rev,
}: {
  sid: string;
  diff: boolean;
  rev: number;
}) {
  const c = useI18n().t.coding;
  const [query, setQuery] = useState("");
  const [content, setContent] = useState(false);
  const [selected, setSelected] = useState("");
  const path = sessionPath(sid);
  const files = useData<string[]>(
    diff
      ? null
      : path +
          "/files?query=" +
          encodeURIComponent(query) +
          "&content=" +
          content,
    rev,
  );
  const changes = useData<Change[]>(diff ? path + "/diff" : null, rev);
  const options = diff
    ? (changes.data || []).map((r) => r.path)
    : files.data || [];
  const current = options.includes(selected) ? selected : options[0];
  const suffix = "?path=" + encodeURIComponent(current || "");
  const file = useData<{ content: string }>(
    !diff && current ? path + "/file" + suffix : null,
    rev,
  );
  const patch = useData<Change[]>(
    diff && current ? path + "/diff" + suffix : null,
    rev,
  );
  const error = files.error || changes.error || file.error || patch.error;
  return (
    <>
      {!diff && (
        <div className="coding-search">
          <input
            aria-label={c.search}
            value={query}
            placeholder={c.search}
            onChange={(e) => setQuery(e.target.value)}
          />
          <label>
            <input
              type="checkbox"
              checked={content}
              onChange={(e) => setContent(e.target.checked)}
            />{" "}
            {c.content}
          </label>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {options.length > 0 ? (
        <select
          value={current}
          aria-label={diff ? c.diff : c.files}
          onChange={(e) => setSelected(e.target.value)}
        >
          {options.map((name, i) => (
            <option key={name} value={name}>
              {diff ? changes.data?.[i].status + "  " : ""}
              {name}
            </option>
          ))}
        </select>
      ) : (
        <p>{c.empty}</p>
      )}
      {!diff && file.data && (
        <CodeViewer
          files={{ [current]: file.data.content }}
          ariaLabel={c.files}
        />
      )}
      {diff &&
        patch.data?.map((row) => (
          <div key={row.path}>
            {(["staged", "working"] as const).map(
              (side) =>
                row[side] && (
                  <section key={side}>
                    <h4>{c[side]}</h4>
                    <DiffViewer text={row[side]} />
                  </section>
                ),
            )}
          </div>
        ))}
    </>
  );
}

export function CodingWorkspace({
  id,
  settings,
}: {
  id?: string;
  settings: boolean;
}) {
  const { t, toggleLocale, locale } = useI18n();
  const c = t.coding;
  const { theme, toggleTheme } = useTheme();
  const [rev, setRev] = useState(0);
  const sessions = useData<Session[]>("coding/sessions", rev);
  const config = useData<{
    provider: string;
    model: string;
    directory: string;
    error?: string;
  }>(settings || !id ? "coding/defaults" : null);
  const { session, entries, error, offline, reload } = useSession(id);
  const [directory, setDirectory] = useState<string | null>(null);
  const draftKey = "coding.draft." + (id || "new");
  const [draft, setDraft] = useState(
    () => sessionStorage.getItem(draftKey) || "",
  );
  const [panel, setPanel] = useState<Panel | null>(null);
  const [failure, setFailure] = useState("");
  const busy = useRef(false);
  const [sending, setSending] = useState(false);
  const lastState = entries.filter((e) => e.kind === "state").at(-1)
    ?.payload.state;
  const active = Boolean(lastState && !terminalStates.includes(lastState));
  const selectedDir =
    directory ??
    localStorage.getItem("coding.lastDirectory") ??
    config.data?.directory ??
    "";
  async function perform(fn: () => Promise<void>) {
    if (busy.current) return;
    busy.current = true;
    setSending(true);
    setFailure("");
    try {
      await fn();
    } catch (e) {
      setFailure((e as Error).message);
    } finally {
      busy.current = false;
      setSending(false);
    }
  }
  function create() {
    void perform(async () => {
      const result = await api<Session>("coding/sessions", {
        directory: selectedDir,
      });
      localStorage.setItem("coding.lastDirectory", result.directory);
      go("coding", result.session_id);
    });
  }
  function send() {
    if (!draft.trim() || active) return;
    void perform(async () => {
      let target = id;
      if (!target) {
        const session = await api<Session>("coding/sessions", {
          directory: selectedDir,
        });
        target = session.session_id;
        localStorage.setItem("coding.lastDirectory", session.directory);
        sessionStorage.setItem("coding.draft." + target, draft);
      }
      const key = "coding.draft." + target + ".request";
      try {
        await api(sessionPath(target) + "/turns", {
          submit_request_id: requestId(key, draft),
          text: draft,
        });
      } catch (err) {
        if (!id) go("coding", target);
        throw err;
      }
      if (!id) {
        sessionStorage.removeItem("coding.draft." + target);
        go("coding", target);
      }
      sessionStorage.removeItem(key);
      sessionStorage.removeItem(draftKey);
      setDraft("");
      setRev((v) => v + 1);
    });
  }
  const history = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  useLayoutEffect(() => {
    if (history.current && follow.current)
      history.current.scrollTop = history.current.scrollHeight;
  }, [entries.length]);
  const revision =
    rev + entries.filter((e) => e.kind === "action_ended").length;
  return (
    <div className="app coding-shell">
      <aside className="app-sidebar">
        <div className="brand">
          <img src="/helix.svg" width="28" height="28" alt="" />
          <div className="brand-title">
            MewCode<small>Coding Workspace</small>
          </div>
        </div>
        <button className="sidebar-new-btn" onClick={() => go("coding")}>
          <Plus size={16} />
          {c.newSession}
        </button>
        <div className="sidebar-projects-head">{c.recent}</div>
        <nav className="app-nav coding-sessions">
          {sessions.data?.map((s) => (
            <a
              href={"#coding/" + s.session_id}
              key={s.session_id}
              className={s.session_id === id ? "selected" : ""}
            >
              <Code2 size={15} />
              <span>
                {s.title || s.directory.split("/").pop()}
                <small>{s.directory}</small>
              </span>
            </a>
          ))}
        </nav>
        {sessions.error && <p role="alert">{sessions.error}</p>}
        <nav className="app-nav coding-nav-bottom">
          <a href={"#coding-settings" + (id ? "/" + id : "")}>
            <Settings size={15} />
            {c.settings}
          </a>
          <a href="#overview">
            <FlaskConical size={15} />
            RSI Center
          </a>
        </nav>
      </aside>
      <main className="coding-main">
        <header className="coding-head">
          <span>Coding Workspace</span>
          <ChevronRight size={14} />
          <b>
            {settings
              ? c.settings
              : session?.directory.split("/").pop() || c.newSession}
          </b>
          <div className="coding-actions">
            <button
              aria-label={t.controls.switchLanguage}
              onClick={toggleLocale}
            >
              <Globe size={16} />
              {locale === "en" ? "中文" : "EN"}
            </button>
            <button
              aria-label={
                theme === "dark"
                  ? t.controls.switchToLight
                  : t.controls.switchToDark
              }
              onClick={toggleTheme}
            >
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
          </div>
        </header>
        <div className="coding-body">
          <div className="coding-center">
            {settings ? (
              <section className="coding-start">
                <h1>{c.readonly}</h1>
                <p>
                  {config.data?.provider} · {config.data?.model}
                </p>
                {(config.error || config.data?.error) && (
                  <p role="alert">{config.error || config.data?.error}</p>
                )}
                <a href={"#coding" + (id ? "/" + id : "")}>Coding Workspace</a>
              </section>
            ) : !id ? (
              <form
                className="coding-start"
                onSubmit={(e) => {
                  e.preventDefault();
                  create();
                }}
              >
                <h1>{c.newSession}</h1>
                <p>{c.choose}</p>
                <label>
                  {c.directory}
                  <input
                    name="directory"
                    required
                    value={selectedDir}
                    onChange={(e) => setDirectory(e.target.value)}
                  />
                </label>
                <button disabled={sending}>{c.open}</button>
                <div className="coding-composer">
                  <textarea
                    aria-label={c.draft}
                    placeholder={c.prompt}
                    value={draft}
                    onChange={(e) => {
                      setDraft(e.target.value);
                      sessionStorage.setItem(draftKey, e.target.value);
                    }}
                  />
                  <button
                    type="button"
                    disabled={sending || !draft.trim() || !selectedDir}
                    onClick={send}
                    className="coding-primary"
                  >
                    <ArrowUp size={15} />
                    {c.send}
                  </button>
                </div>
              </form>
            ) : (
              <>
                <div className="coding-session-head">
                  <strong>{session?.directory}</strong>
                  <div className="coding-actions">
                    {(Object.keys(panels) as Panel[]).map((k) => {
                      const Icon = panels[k];
                      return (
                        <button
                          key={k}
                          aria-pressed={panel === k}
                          onClick={() => setPanel((p) => (p === k ? null : k))}
                        >
                          <Icon size={14} />
                          {c[k]}
                        </button>
                      );
                    })}
                  </div>
                </div>
                {offline && <p role="status">{c.disconnect}</p>}
                {session?.available === false && (
                  <p role="alert">{c.unavailable}</p>
                )}
                <div
                  className="coding-history"
                  ref={history}
                  onScroll={(e) => {
                    const node = e.currentTarget;
                    follow.current =
                      node.scrollHeight - node.scrollTop - node.clientHeight <
                      48;
                  }}
                >
                  <CodingTimeline sid={id} entries={entries} />
                </div>
                <form
                  className="coding-composer"
                  onSubmit={(e) => {
                    e.preventDefault();
                    send();
                  }}
                >
                  <textarea
                    aria-label={c.draft}
                    placeholder={c.prompt}
                    value={draft}
                    onChange={(e) => {
                      setDraft(e.target.value);
                      sessionStorage.setItem(draftKey, e.target.value);
                    }}
                    onKeyDown={(e) => {
                      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                        e.preventDefault();
                        send();
                      }
                    }}
                  />
                  <button
                    className="coding-primary"
                    disabled={
                      !session?.available || sending || active || !draft.trim()
                    }
                  >
                    <ArrowUp size={15} />
                    {c.send}
                  </button>
                </form>
              </>
            )}
            {(failure || error) && (
              <div role="alert">
                {failure || error}{" "}
                {error && <button onClick={reload}>{c.again}</button>}
              </div>
            )}
          </div>
          {!settings && id && panel && (
            <aside className="coding-panel">
              <header>
                <h3>{c[panel]}</h3>
                <button onClick={() => setRev((v) => v + 1)}>
                  {c.refresh}
                </button>
                <button
                  aria-label={c.closePanel}
                  onClick={() => setPanel(null)}
                >
                  <X size={14} />
                </button>
              </header>
              {panel === "files" || panel === "diff" ? (
                <FilesPanel
                  key={panel}
                  sid={id}
                  diff={panel === "diff"}
                  rev={revision}
                />
              ) : (
                <CodingTimeline sid={id} entries={entries} filter={panel} />
              )}
            </aside>
          )}
        </div>
      </main>
    </div>
  );
}
