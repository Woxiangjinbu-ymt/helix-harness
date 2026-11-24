export type DiffRow = {
  type: "add" | "del" | "hunk" | "ctx" | "meta";
  oldLine?: number;
  newLine?: number;
  text: string;
};

export type DiffFile = {
  path: string;
  status: "modified" | "added" | "deleted";
  added: number;
  removed: number;
  rows: DiffRow[];
};

export function parseDiff(text: string): DiffFile[] {
  if (!text.trim()) return [];
  const lines = text.replace(/\n$/, "").split("\n");
  const files: DiffFile[] = [];
  let current: DiffFile | null = null;
  let oldLn = 0;
  let newLn = 0;

  const ensureFile = (path: string) => {
    current = { path, status: "modified", added: 0, removed: 0, rows: [] };
    files.push(current);
    return current;
  };

  for (const line of lines) {
    if (line.startsWith("diff --git ")) {
      const m = line.match(/^diff --git a\/(.+?) b\/(.+)$/);
      ensureFile(m ? m[2] : line.slice(11));
      continue;
    }
    if (!current) {
      if (!line) continue;
      ensureFile("patch");
    }
    const target = current!;
    if (line === "new file") {
      target.status = "added";
      continue;
    }
    if (line === "deleted file") {
      target.status = "deleted";
      continue;
    }
    if (line.startsWith("--- ") || line.startsWith("+++ ")) {
      continue;
    }
    if (line.startsWith("@@")) {
      const m = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (m) {
        oldLn = Number(m[1]);
        newLn = Number(m[2]);
      }
      target.rows.push({ type: "hunk", text: line });
      continue;
    }
    if (line.startsWith("+")) {
      target.added += 1;
      target.rows.push({ type: "add", newLine: newLn++, text: line });
      continue;
    }
    if (line.startsWith("-")) {
      target.removed += 1;
      target.rows.push({ type: "del", oldLine: oldLn++, text: line });
      continue;
    }
    if (line.startsWith("\\")) {
      target.rows.push({ type: "meta", text: line });
      continue;
    }
    if (line === "" && target.rows.length === 0) continue;
    target.rows.push({
      type: "ctx",
      oldLine: oldLn || undefined,
      newLine: newLn || undefined,
      text: line || " ",
    });
    if (oldLn) oldLn += 1;
    if (newLn) newLn += 1;
  }

  return files;
}
