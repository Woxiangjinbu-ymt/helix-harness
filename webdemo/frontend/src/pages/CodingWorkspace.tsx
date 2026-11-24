import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowUp,
  ChevronRight,
  Code2,
  Files,
  FolderOpen,
  PanelLeft,
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
import { CodingSettings } from "./CodingSettings";
import { CodingDirectoryPicker } from "../components/CodingDirectoryPicker";
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
  const { session, entries, error, offline, reload } = useSession(id);
  const draftKey = "coding.draft." + (id || "new");
  const [draft, setDraft] = useState(
    () => sessionStorage.getItem(draftKey) || "",
  );
  const [panel, setPanel] = useState<Panel | null>(null);
  const [picker, setPicker] = useState<"open" | "send" | null>(null);
  const [sidebar, setSidebar] = useState(false);
  const [failure, setFailure] = useState("");
  const busy = useRef(false);
  const [sending, setSending] = useState(false);
  const [pending, setPending] = useState<{
    request: string;
    text: string;
  } | null>(null);
  const lastState = entries.filter((e) => e.kind === "state").at(-1)
    ?.payload.state;
  const active = Boolean(lastState && !terminalStates.includes(lastState));
  const userRequests = entries
    .filter((e) => e.kind === "user")
    .map((e) => e.payload.submit_request_id);
  const showPending = pending && !userRequests.includes(pending.request);
  const empty = !id || (session && !entries.length && !pending);
  const folderName = session?.directory.split("/").pop() || "/";
  const recent = sessions.data?.filter(
    (s) => s.title || sessionStorage.getItem("coding.draft." + s.session_id),
  );
  const input = useRef<HTMLTextAreaElement>(null);
  function changeDraft(text: string) {
    setDraft(text);
    sessionStorage.setItem(draftKey, text);
  }
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
  async function openDirectory(directory: string) {
    const profile = localStorage.getItem("coding.profile") || "";
    const key = "coding.new.request";
    const result = await api<Session>("coding/sessions", {
      directory,
      profile_id: profile,
      request_id: requestId(key, JSON.stringify([directory, profile])),
    });
    const nextKey = "coding.draft." + result.session_id;
    sessionStorage.setItem(nextKey, draft);
    if (picker === "send") sessionStorage.setItem(nextKey + ".send", "1");
    localStorage.setItem("coding.lastDirectory", result.directory);
    sessionStorage.removeItem(key);
    sessionStorage.removeItem(draftKey);
    go("coding", result.session_id);
  }
  function send() {
    if (!draft.trim() || active || busy.current || showPending) return;
    if (!id) {
      setPicker("send");
      return;
    }
    if (!session?.available) return;
    const key = draftKey + ".request";
    const submission = requestId(key, draft);
    setPending({ request: submission, text: draft });
    void perform(async () => {
      try {
        await api(sessionPath(id) + "/turns", {
          submit_request_id: submission,
          text: draft,
        });
        // 回执或事件任一先到，都可以确认接收。等待事件接替临时气泡。
        if (sessionStorage.getItem(draftKey) === draft) changeDraft("");
        sessionStorage.removeItem(key);
        setRev((v) => v + 1);
      } catch (err) {
        setPending(null);
        if (sessionStorage.getItem(key)) throw err;
      }
    });
  }
  useEffect(() => {
    if (!session?.available || settings) return;
    input.current?.focus();
    if (sessionStorage.getItem(draftKey + ".send")) {
      sessionStorage.removeItem(draftKey + ".send");
      send();
    }
  }, [session?.session_id, settings]);
  useEffect(() => {
    const key = draftKey + ".request";
    const saved = sessionStorage.getItem(key);
    if (saved) {
      const { id: request, content } = JSON.parse(saved);
      if (userRequests.includes(request)) {
        if (sessionStorage.getItem(draftKey) === content) changeDraft("");
        sessionStorage.removeItem(key);
        setFailure("");
        setRev((v) => v + 1);
      }
    }
    if (pending && userRequests.includes(pending.request)) setPending(null);
  }, [entries, pending]);
  const history = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  useLayoutEffect(() => {
    if (history.current && follow.current)
      history.current.scrollTop = history.current.scrollHeight;
  }, [entries.length, showPending]);
  const revision =
    rev + entries.filter((e) => e.kind === "action_ended").length;
  return (
    <div className={"app coding-shell" + (sidebar ? " sidebar-open" : "")}>
      {sidebar && (
        <button
          className="coding-scrim"
          aria-label={t.controls.collapseSidebar}
          onClick={() => setSidebar(false)}
        />
      )}
      <aside className="app-sidebar">
        <div className="brand">
          <img src="/helix.svg" width="28" height="28" alt="" />
          <div className="brand-title">
            MewCode<small>Coding Workspace</small>
          </div>
        </div>
        <button
          className="sidebar-new-btn"
          onClick={() => {
            setSidebar(false);
            go("coding");
          }}
        >
          <Plus size={16} />
          {c.newSession}
        </button>
        {Boolean(recent?.length) && (
          <div className="sidebar-projects-head">{c.recent}</div>
        )}
        <nav className="app-nav coding-sessions">
          {recent?.map((s) => (
            <a
              href={"#coding/" + s.session_id}
              key={s.session_id}
              className={s.session_id === id ? "selected" : ""}
            >
              <Code2 size={15} />
              <span>
                {s.title ||
                  sessionStorage.getItem("coding.draft." + s.session_id)}
                <small title={s.directory}>
                  {s.directory.split("/").pop() || "/"}
                </small>
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
          <button
            className="coding-menu"
            aria-label={t.controls.expandSidebar}
            onClick={() => setSidebar(true)}
          >
            <PanelLeft size={17} />
          </button>
          <span>Coding Workspace</span>
          {settings && (
            <>
              <ChevronRight size={14} />
              <b>{c.settings}</b>
            </>
          )}
          {!settings && session && (
            <details className="coding-location">
              <summary>
                <FolderOpen size={15} />
                {c.directory} · {folderName}
              </summary>
              <span>{session.directory}</span>
            </details>
          )}
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
              <CodingSettings sid={id || ""} />
            ) : (
              <>
                {id && session && (
                  <div className="coding-tools">
                    {(Object.keys(panels) as Panel[])
                      .filter(
                        (k) => k !== "diff" || session.workspace_kind === "git",
                      )
                      .map((k) => {
                        const Icon = panels[k];
                        return (
                          <button
                            key={k}
                            aria-pressed={panel === k}
                            onClick={() =>
                              setPanel((p) => (p === k ? null : k))
                            }
                          >
                            <Icon size={14} />
                            {c[k]}
                          </button>
                        );
                      })}
                  </div>
                )}
                {id && active && (
                  <button
                    disabled={sending || lastState === "cancelling"}
                    onClick={() => {
                      const turn = entries
                        .filter((e) => e.kind === "state")
                        .at(-1)!.turn_id;
                      void perform(async () => {
                        await api(
                          sessionPath(id) + "/turns/" + turn + "/cancel",
                          {
                            request_id: requestId(
                              "coding.cancel." + turn,
                              turn,
                            ),
                          },
                        );
                      });
                    }}
                  >
                    {locale === "zh" ? "取消轮次" : "Cancel turn"}
                  </button>
                )}
                {id && !session && !error && (
                  <p role="status">{t.controls.loading}</p>
                )}
                {offline && <p role="status">{c.disconnect}</p>}
                {session?.available === false && (
                  <p role="alert">{c.unavailable}</p>
                )}
                {!empty && id && (
                  <div
                    className="coding-history"
                    ref={history}
                    onScroll={(e) => {
                      const n = e.currentTarget;
                      follow.current =
                        n.scrollHeight - n.scrollTop - n.clientHeight < 48;
                    }}
                  >
                    <CodingTimeline
                      sid={id}
                      entries={entries}
                      onDiff={
                        session?.workspace_kind === "git"
                          ? () => setPanel("diff")
                          : undefined
                      }
                    />
                    {showPending && pending && (
                      <div className="coding-pending" role="status">
                        <div className="coding-user">{pending.text}</div>
                        <small>{sending ? c.sending : c.accepted}</small>
                      </div>
                    )}
                  </div>
                )}
                <div
                  className={"coding-compose-area" + (empty ? " is-empty" : "")}
                >
                  {empty && <h1>{c.startTitle}</h1>}
                  {(failure || error) && (
                    <div className="coding-inline-error" role="alert">
                      {(failure || error) === "storage_error"
                        ? c.storageFault
                        : failure || error}
                      {error ? (
                        <button onClick={reload}>{c.again}</button>
                      ) : (
                        draft.trim() && (
                          <button disabled={sending || active} onClick={send}>
                            {c.again}
                          </button>
                        )
                      )}
                    </div>
                  )}
                  <form
                    className="coding-composer"
                    onSubmit={(e) => {
                      e.preventDefault();
                      send();
                    }}
                  >
                    <textarea
                      ref={input}
                      aria-label={c.draft}
                      autoFocus
                      rows={2}
                      placeholder={c.prompt}
                      value={draft}
                      disabled={Boolean(id) && !session}
                      readOnly={sending}
                      onChange={(e) => changeDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (
                          e.key === "Enter" &&
                          !e.shiftKey &&
                          !e.altKey &&
                          !e.nativeEvent.isComposing &&
                          e.nativeEvent.keyCode !== 229
                        ) {
                          e.preventDefault();
                          send();
                        }
                      }}
                    />
                    <div className="coding-compose-tools">
                      {!id && (
                        <button
                          type="button"
                          className="coding-folder-button"
                          onClick={() => setPicker("open")}
                        >
                          <FolderOpen size={16} />
                          {c.choose}
                        </button>
                      )}
                      <button
                        className="coding-primary coding-send"
                        aria-label={c.send}
                        title={c.send}
                        disabled={
                          (Boolean(id) && !session?.available) ||
                          sending ||
                          active ||
                          Boolean(showPending) ||
                          !draft.trim()
                        }
                      >
                        <ArrowUp size={19} />
                      </button>
                    </div>
                  </form>
                </div>
                {picker && (
                  <CodingDirectoryPicker
                    onClose={() => setPicker(null)}
                    onOpen={openDirectory}
                  />
                )}
              </>
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
