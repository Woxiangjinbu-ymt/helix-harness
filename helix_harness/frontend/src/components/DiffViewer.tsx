import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, FileDiff } from "lucide-react";
import { useI18n } from "../i18n/context";
import { parseDiff } from "../lib/diff";
import { CopyButton, Empty } from "./ui";

export function DiffViewer({ text }: { text: string }) {
  const { t } = useI18n();
  const files = useMemo(() => parseDiff(text), [text]);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  if (!text.trim() || !files.length) {
    return (
      <Empty icon={<FileDiff size={22} />}>{t.workspace.noFileChanges}</Empty>
    );
  }

  const totalAdd = files.reduce((s, f) => s + f.added, 0);
  const totalDel = files.reduce((s, f) => s + f.removed, 0);

  return (
    <div className="diff-viewer">
      <div className="diff-summary-bar">
        <div className="diff-stats">
          <span>{t.workspace.filesChanged(files.length)}</span>
          <span className="diff-stat-add">+{totalAdd}</span>
          <span className="diff-stat-del">-{totalDel}</span>
        </div>
        <CopyButton text={text} />
      </div>
      <div className="diff">
        {files.map((file) => {
          const isClosed = !!collapsed[file.path];
          return (
            <div className="diff-file-card" key={file.path}>
              <button
                type="button"
                className="diff-file-head"
                onClick={() =>
                  setCollapsed((c) => ({ ...c, [file.path]: !c[file.path] }))
                }
              >
                <span className="row">
                  {isClosed ? (
                    <ChevronRight size={14} />
                  ) : (
                    <ChevronDown size={14} />
                  )}
                  <span>{file.path}</span>
                </span>
                <span className="diff-stats">
                  {file.added > 0 && (
                    <span className="diff-stat-add">+{file.added}</span>
                  )}
                  {file.removed > 0 && (
                    <span className="diff-stat-del">-{file.removed}</span>
                  )}
                </span>
              </button>
              {!isClosed &&
                file.rows.map((row, idx) => (
                  <div className={"diff-row " + row.type} key={idx}>
                    <span className="diff-gutter">
                      {row.type === "hunk" ? "…" : row.oldLine ?? ""}
                    </span>
                    <span className="diff-gutter">
                      {row.type === "hunk" ? "…" : row.newLine ?? ""}
                    </span>
                    <span className="diff-code">{row.text}</span>
                  </div>
                ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
