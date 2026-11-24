import { useCallback, useEffect, useState } from "react";
import type { EventRow } from "../types";

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(
    "/api/" + path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-RSI-Client": "console",
          },
          body: JSON.stringify(body),
        },
  );
  if (!r.ok) {
    const v = await r.json().catch(() => ({}));
    throw Error(
      typeof v.detail === "string"
        ? v.detail
        : v.detail?.message || "Request failed (" + r.status + ")",
    );
  }
  return r.json();
}

export function useData<T>(path: string | null, rev = 0) {
  const [state, set] = useState<{
    path: string | null;
    data?: T;
    error?: string;
    loading: boolean;
  }>({ path: null, loading: Boolean(path) });

  useEffect(() => {
    let live = true;
    if (!path) {
      set({ path: null, loading: false });
      return;
    }
    set((prev) =>
      prev.path === path
        ? { ...prev, loading: true, error: undefined }
        : { path, loading: true },
    );
    api<T>(path)
      .then((data) => live && set({ path, data, loading: false }))
      .catch((e) => live && set({ path, error: e.message, loading: false }));
    return () => {
      live = false;
    };
  }, [path, rev]);

  return state.path === path ? state : { path, loading: Boolean(path) };
}

export const go = (page: string, id?: string) => {
  location.hash = page + (id ? "/" + encodeURIComponent(id) : "");
};

export function useRoute() {
  const parse = () => location.hash.slice(1).split("/").map(decodeURIComponent);
  const [route, set] = useState(parse);
  useEffect(() => {
    const on = () => set(parse());
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function useJournal(group: string, job?: string) {
  const [state, set] = useState<{
    job?: string;
    events: EventRow[];
    connected: boolean;
  }>({ events: [], connected: false });

  useEffect(() => {
    if (!job) return;
    set({ job, events: [], connected: false });
    const s = new EventSource(
      "/api/" + group + "/" + encodeURIComponent(job) + "/events",
    );
    s.onopen = () => set((x) => ({ ...x, connected: true }));
    s.onmessage = (m) => {
      const e: EventRow = JSON.parse(m.data);
      const done = [
        "run_completed",
        "run_failed",
        "run_cancelled",
        "run_interrupted",
      ].includes(e.event_type);
      set((x) => ({
        job,
        connected: !done,
        events: x.events.some((v) => v.event_id === e.event_id)
          ? x.events
          : [...x.events, e],
      }));
      if (done) s.close();
    };
    s.onerror = () => set((x) => ({ ...x, connected: false }));
    return () => s.close();
  }, [group, job]);

  return state.job === job ? state : { events: [], connected: false };
}

export function useRevision() {
  const [rev, set] = useState(0);
  return [rev, useCallback(() => set((r) => r + 1), [])] as const;
}
