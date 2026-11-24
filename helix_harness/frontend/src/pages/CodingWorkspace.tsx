import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
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
  Sparkles,
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

import { CodingComposer, CodingTheme } from "../components/CodingComposer";
import { discardFile, draftStorage, uploadFiles } from "../lib/composer";
import type { DraftFile, Preferences, Profile } from "../lib/composer";

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
    () => draftStorage.getItem(draftKey) || "",
  );
  const profiles = useData<Profile[]>("coding/profiles", rev);
  const [edits, setEdits] = useState<Preferences>(() =>
    JSON.parse(draftStorage.getItem(draftKey + ".options") || "{}"),
  );
  const [files, setFiles] = useState<DraftFile[]>(() =>
    JSON.parse(draftStorage.getItem(draftKey + ".files") || "[]"),
  );
  const prefs = { ...session?.composer, ...edits };
  function changePrefs(change: Preferences) {
    setEdits((old) => {
      const next = { ...old, ...change };
      draftStorage.setItem(draftKey + ".options", JSON.stringify(next));
      return next;
    });
  }
  function changeFiles(change: (old: DraftFile[]) => DraftFile[]) {
    setFiles((old) => {
      const next = change(old);
      draftStorage.setItem(draftKey + ".files", JSON.stringify(next));
      return next;
    });
  }
  function acknowledge(request: string) {
    const key = draftKey + ".request";
    const saved = draftStorage.getItem(key);
    if (!saved) return;
    const receipt = JSON.parse(saved);
    if (receipt.id !== request) return;
    // Text-only requests created by an earlier version used the draft itself.
    let sent: { text: string; attachment_ids?: string[] } = {
      text: receipt.content,
    };
    try {
      const parsed = JSON.parse(receipt.content);
      if (typeof parsed?.text === "string") sent = parsed;
    } catch {
      /* legacy draft */
    }
    if ((draftStorage.getItem(draftKey) || "") === sent.text) changeDraft("");
    const used = sent.attachment_ids || [];
    if (used.length) {
      changeFiles((old) => old.filter((f) => !used.includes(f.id)));
      for (const fid of used) void discardFile(fid).catch(() => {});
    }
    draftStorage.removeItem(key);
    const pendingOptions = draftStorage.getItem(draftKey + ".options");
    if (
      pendingOptions &&
      Object.entries(JSON.parse(pendingOptions)).every(
        ([k, v]) =>
          JSON.stringify((sent as Record<string, unknown>)[k]) ===
          JSON.stringify(v),
      )
    ) {
      draftStorage.removeItem(draftKey + ".options");
    }
    setFailure("");
    setRev((v) => v + 1);
  }
  const [panel, setPanel] = useState<Panel | null>(null);
  const [picker, setPicker] = useState<"open" | "reference" | "send" | null>(
    null,
  );
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
    (s) => s.title || draftStorage.getItem("coding.draft." + s.session_id),
  );
  const input = useRef<HTMLTextAreaElement>(null);
  function changeDraft(text: string) {
    setDraft(text);
    draftStorage.setItem(draftKey, text);
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
    const profile =
      localStorage.getItem("coding.profile") ||
      profiles.data?.find((p) => p.source === "native")?.profile_id ||
      edits.model_profile_id ||
      "";
    const key = "coding.new.request";
    const result = await api<Session>("coding/sessions", {
      directory,
      profile_id: profile,
      request_id: requestId(
        key,
        JSON.stringify([directory, profile]),
        draftStorage,
      ),
    });
    const nextKey = "coding.draft." + result.session_id;
    draftStorage.setItem(nextKey, draft);
    draftStorage.setItem(nextKey + ".files", JSON.stringify(files));
    draftStorage.setItem(nextKey + ".options", JSON.stringify(edits));
    draftStorage.removeItem(draftKey + ".files");
    draftStorage.removeItem(draftKey + ".options");
    if (picker === "reference")
      sessionStorage.setItem(nextKey + ".reference", "1");
    if (picker === "send") sessionStorage.setItem(nextKey + ".send", "1");
    localStorage.setItem("coding.lastDirectory", result.directory);
    draftStorage.removeItem(key);
    draftStorage.removeItem(draftKey);
    go("coding", result.session_id);
  }
  function send() {
    if (
      (!draft.trim() && !files.length) ||
      active ||
      busy.current ||
      showPending
    )
      return;
    if (!id) {
      setPicker("send");
      return;
    }
    if (!session?.available) return;
    const key = draftKey + ".request";
    const payload = {
      text: draft,
      ...edits,
      ...(files.length ? { attachment_ids: files.map((f) => f.id) } : {}),
    };
    const submission = requestId(key, JSON.stringify(payload), draftStorage);
    setPending({
      request: submission,
      text: draft || files.map((f) => f.name).join(", "),
    });
    void perform(async () => {
      try {
        await uploadFiles(id, files);
        await api(sessionPath(id) + "/turns", {
          ...payload,
          submit_request_id: submission,
        });
        acknowledge(submission);
      } catch (err) {
        setPending(null);
        if (draftStorage.getItem(key)) throw err;
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
    const saved = draftStorage.getItem(key);
    if (saved) {
      const { id: request } = JSON.parse(saved);
      if (userRequests.includes(request)) acknowledge(request);
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
      <CodingTheme />
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
            Helix Harness<small>Coding Workspace</small>
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
                  draftStorage.getItem("coding.draft." + s.session_id)}
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
          <a href="#repo-rsi">
            <Sparkles size={15} />
            {t.nav.repoRsi}
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
                        (draft.trim() || files.length > 0) && (
                          <button disabled={sending || active} onClick={send}>
                            {c.again}
                          </button>
                        )
                      )}
                    </div>
                  )}
                  <CodingComposer
                    sid={id}
                    session={session}
                    entries={entries}
                    input={input}
                    draft={draft}
                    changeDraft={changeDraft}
                    send={send}
                    cancel={() => {
                      const turn = entries
                        .filter((e) => e.kind === "state")
                        .at(-1)?.turn_id;
                      if (id && turn && lastState !== "cancelling")
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
                    active={active}
                    disabled={
                      Boolean(id && !session?.available) ||
                      sending ||
                      Boolean(showPending) ||
                      (!draft.trim() && !files.length)
                    }
                    busy={sending}
                    prefs={prefs}
                    changePrefs={changePrefs}
                    files={files}
                    changeFiles={changeFiles}
                    fail={setFailure}
                    profiles={profiles.data}
                    profilesError={profiles.error}
                    chooseDirectory={(reference) =>
                      setPicker(reference ? "reference" : "open")
                    }
                  />
                  {!id && (
                    <button
                      className="coding-folder-button"
                      onClick={() => setPicker("open")}
                    >
                      <FolderOpen size={15} />
                      {c.choose}
                    </button>
                  )}
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
