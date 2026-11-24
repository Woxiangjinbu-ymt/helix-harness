import { useEffect, useState } from "react";
import { api } from "./api";

export type Session = {
  session_id: string;
  directory: string;
  title: string;
  available?: boolean;
  profile_id: string;
  composer?: import("./composer").Preferences;
  workspace_kind: "git" | "folder";
};
export type Payload = {
  submit_request_id?: string;
  profile_id?: string;
  used?: number;
  window?: number;
  threshold?: number;
  thinking_level?: string;
  attachments?: import("./composer").Attachment[];
  input_tokens?: number;
  output_tokens?: number;
  text?: string;
  state?: string;
  reason?: string;
  tool?: string;
  args?: Record<string, unknown>;
  call_id?: string;
  permission_id?: string;
  action_id?: string;
  message_id?: string;
  description?: string;
  scope?: string;
  directory?: string;
  decision?: string;
  feedback?: string;
  command?: string;
  cwd?: string;
  output?: string;
  is_error?: boolean;
  exit_code?: number | null;
  path?: string;
  after?: string;
  uncertain?: boolean;
  truncated?: boolean;
  provider?: string;
  model?: string;
  hook_id?: string;
  error_type?: string;
  extension?: string;
  source?: string;
  name?: string;
  status?: string;
  error?: string;
  tools?: string[];
  holder_session_id?: string;
  rule_error?: string;
  changes?: { path: string; call_id: string; uncertain: boolean }[];
};
export type Entry = {
  seq: number;
  recorded_at: string;
  turn_id: string;
  kind: string;
  payload: Payload;
};
type View = { session: Session; events: Entry[]; through_seq: number };
export const sessionPath = (id: string) =>
  "coding/sessions/" + encodeURIComponent(id);
export type Answer = "allow" | "allow_always" | "deny";
export const terminalStates = [
  "completed",
  "failed",
  "interrupted",
  "cancelled",
];

export function useSession(id?: string) {
  const [session, setSession] = useState<Session>();
  const [entries, setEntries] = useState<Entry[]>([]);
  const [error, setError] = useState("");
  const [offline, setOffline] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!id) return;
    let live = true;
    let source: EventSource | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let cursor = 0;
    const path = sessionPath(id);
    setError("");
    api<View>(path + "/view")
      .then((view) => {
        if (!live) return;
        setSession(view.session);
        setEntries(view.events);
        cursor = view.through_seq;
        let applied = view.events;
        function connect() {
          if (!live) return;
          const feed = new EventSource(
            "/api/" +
              path +
              "/events?after=" +
              encodeURIComponent(id + "/" + cursor),
          );
          source = feed;
          function retryFeed() {
            if (!live || source !== feed) return;
            feed.close();
            source = undefined;
            setOffline(true);
            timer = setTimeout(connect, 500);
          }
          feed.onopen = () => {
            if (live) {
              setOffline(false);
              setError("");
            }
          };
          feed.onerror = retryFeed;
          feed.onmessage = (msg) => {
            if (!live || feed !== source) return;
            try {
              const entry = JSON.parse(msg.data);
              if (
                entry.session_id !== id ||
                !Number.isSafeInteger(entry.seq) ||
                msg.lastEventId !== id + "/" + entry.seq ||
                !entry.kind ||
                !entry.payload
              )
                throw Error("Invalid session event");
              if (entry.seq <= cursor) return;
              applied = [...applied, entry];
              setEntries(applied);
              cursor = entry.seq;
            } catch {
              retryFeed();
            }
          };
          feed.addEventListener("storage_error", () => {
            setError("storage_error");
            retryFeed();
          });
          feed.addEventListener("reset_required", () => {
            if (live) {
              feed.close();
              setRetry((r) => r + 1);
            }
          });
        }
        connect();
      })
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
      clearTimeout(timer);
      source?.close();
    };
  }, [id, retry]);
  return {
    session,
    entries,
    error,
    offline,
    reload: () => setRetry((r) => r + 1),
  };
}

export function requestId(
  key: string,
  content: string,
  storage: Pick<Storage, "getItem" | "setItem"> = sessionStorage,
) {
  const old = storage.getItem(key);
  if (old) {
    const saved = JSON.parse(old);
    if (saved.content === content) return saved.id as string;
  }
  const id = crypto.randomUUID();
  storage.setItem(key, JSON.stringify({ id, content }));
  return id;
}
