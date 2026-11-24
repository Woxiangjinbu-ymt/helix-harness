import { sessionPath } from "./coding";

export type Preferences = {
  model_profile_id?: string;
  permission_mode?: string;
  thinking_level?: string;
  skills?: string[];
};
export type Profile = {
  profile_id: string;
  model: string;
  provider: string;
  source: string;
  configured: boolean;
  permission_mode: string;
  context_window: number;
  compact_threshold: number;
  window_source: string;
  thinking_levels: string[];
  vision: boolean;
};
export type Attachment = {
  id: string;
  name: string;
  mime: string;
  size: number;
};
export type DraftFile = Attachment & { local?: boolean };

// Recover a draft after closing its tab. Read legacy per-tab drafts on first access.
export const draftStorage = {
  getItem(key: string) {
    const current = localStorage.getItem(key);
    if (current !== null) return current;
    const old = sessionStorage.getItem(key);
    if (old !== null) {
      localStorage.setItem(key, old);
      sessionStorage.removeItem(key);
    }
    return old;
  },
  setItem(key: string, value: string) {
    localStorage.setItem(key, value);
    sessionStorage.removeItem(key);
  },
  removeItem(key: string) {
    localStorage.removeItem(key);
    sessionStorage.removeItem(key);
  },
};

let database: Promise<IDBDatabase> | undefined;
function filesDB() {
  if (!database)
    database = new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open("mewcode-composer", 1);
      req.onupgradeneeded = () => req.result.createObjectStore("files");
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => {
        database = undefined;
        reject(req.error);
      };
    });
  return database;
}
async function withFile<T>(
  mode: IDBTransactionMode,
  action: (store: IDBObjectStore) => IDBRequest<T>,
) {
  const db = await filesDB();
  return new Promise<T>((resolve, reject) => {
    const tx = db.transaction("files", mode);
    const req = action(tx.objectStore("files"));
    tx.oncomplete = () => resolve(req.result);
    tx.onabort = () =>
      reject(tx.error || Error("Attachment storage unavailable"));
    tx.onerror = () => reject(tx.error);
  });
}
export async function keepFile(file: File): Promise<DraftFile> {
  const id = crypto.randomUUID();
  await withFile("readwrite", (s) => s.put(file, id));
  return {
    id,
    name: file.name,
    size: file.size,
    mime: file.type || "text/plain",
    local: true,
  };
}
export const loadFile = (id: string): Promise<File | undefined> =>
  withFile("readonly", (s) => s.get(id));
export const discardFile = (id: string) =>
  withFile("readwrite", (s) => s.delete(id));
export const attachmentURL = (sid: string, id: string) =>
  "/api/" + sessionPath(sid) + "/attachments/" + encodeURIComponent(id);

export async function uploadFiles(sid: string, files: DraftFile[]) {
  for (const item of files) {
    if (!item.local) continue;
    const body = await loadFile(item.id);
    if (!body) throw Error("请重新选择附件 · " + item.name);
    const r = await fetch(
      attachmentURL(sid, item.id) + "?name=" + encodeURIComponent(item.name),
      {
        method: "POST",
        headers: {
          "X-RSI-Client": "console",
          "Content-Type": "application/octet-stream",
        },
        body,
      },
    );
    if (!r.ok)
      throw Error((await r.json().catch(() => ({}))).detail || "Upload failed");
  }
}
