import { useState } from "react";
import { FileCode2 } from "lucide-react";
import { useI18n } from "../i18n/context";
import { highlightLine } from "../lib/format";
import { CopyButton, Empty } from "./ui";

export function CodeViewer({
  files,
  selectedFile,
  onSelectFile,
  ariaLabel,
  emptyText,
}: {
  files: Record<string, string>;
  selectedFile?: string;
  onSelectFile?: (path: string) => void;
  ariaLabel?: string;
  emptyText?: string;
}) {
  const { t } = useI18n();
  const [internal, setInternal] = useState("");
  const keys = Object.keys(files);
  const current = selectedFile && keys.includes(selectedFile)
    ? selectedFile
    : keys.includes(internal)
      ? internal
      : keys[0];

  if (!current) {
    return (
      <Empty icon={<FileCode2 size={22} />}>
        {emptyText || t.workspace.noSealedFiles}
      </Empty>
    );
  }

  const content = files[current] || "";
  const lines = content.split("\n");

  const handleChange = (next: string) => {
    setInternal(next);
    onSelectFile?.(next);
  };

  return (
    <div className="code-viewer">
      <div className="code-toolbar">
        <select
          aria-label={ariaLabel || t.workspace.artifactFileAria}
          value={current}
          onChange={(e) => handleChange(e.target.value)}
        >
          {keys.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
        <span className="code-meta">{t.workspace.linesCount(lines.length)}</span>
        <CopyButton text={content} />
      </div>
      <pre className="code">
        {lines.map((line, i) => {
          const tokens = highlightLine(line, current);
          return (
            <div className="code-line" key={i}>
              <span className="code-ln">{i + 1}</span>
              <span className="code-src">
                {tokens.map((tok, j) =>
                  tok.kind === "plain" ? (
                    tok.text
                  ) : (
                    <span className={"syn-" + tok.kind} key={j}>
                      {tok.text}
                    </span>
                  ),
                )}
              </span>
            </div>
          );
        })}
      </pre>
    </div>
  );
}
