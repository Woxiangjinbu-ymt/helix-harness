import { useEffect, useState } from "react";
import { api } from "./api";

export type Session = {
  session_id: string;
  directory: string;
  title: string;
  available?: boolean;
};
export type Payload = {
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
  holder_session_id?: string;
  rule_error?: string;
  changes?: { path: string; call_id: string; uncertain: boolean }[];
};
export type Entry = {
  seq: number;
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
    const path = sessionPath(id);
    setError("");
    api<View>(path + "/view")
      .then((view) => {
        if (!live) return;
        setSession(view.session);
        setEntries(view.events);
        const cursor = encodeURIComponent(id + "/" + view.through_seq);
        source = new EventSource("/api/" + path + "/events?after=" + cursor);
        source.onopen = () => live && setOffline(false);
        source.onerror = () => live && setOffline(true);
        source.onmessage = (msg) => {
          if (!live) return;
          const entry: Entry = JSON.parse(msg.data);
          setEntries((prev) =>
            !prev.length || prev[prev.length - 1].seq < entry.seq
              ? [...prev, entry]
              : prev,
          );
        };
        source.addEventListener(
          "reset_required",
          () => live && setRetry((r) => r + 1),
        );
      })
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
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

export function requestId(key: string, content: string) {
  const old = sessionStorage.getItem(key);
  if (old) {
    const saved = JSON.parse(old);
    if (saved.content === content) return saved.id as string;
  }
  const id = crypto.randomUUID();
  sessionStorage.setItem(key, JSON.stringify({ id, content }));
  return id;
}
