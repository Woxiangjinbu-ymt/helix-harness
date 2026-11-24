import type { Locale } from "../i18n/types";

export const num = (v: number | null | undefined) =>
  v == null
    ? "—"
    : new Intl.NumberFormat("en", { maximumFractionDigits: 2 }).format(v);

export const elapsed = (v: number | null | undefined) =>
  v == null ? "—" : (v / 1000).toFixed(1) + " s";

export const formatDate = (v?: string, locale: Locale = "en") =>
  v
    ? new Date(v).toLocaleString(locale === "zh" ? "zh-CN" : "en-GB", {
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";

export function relativeTime(v?: string, locale: Locale = "en") {
  if (!v) return "";
  const diff = Math.max(0, Date.now() - Date.parse(v));
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return locale === "zh" ? "刚刚" : "now";
  if (mins < 60) return locale === "zh" ? `${mins}分` : `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return locale === "zh" ? `${hrs}时` : `${hrs}h`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return locale === "zh" ? `${days}天` : `${days}d`;
  return formatDate(v, locale);
}

export const short = (s?: string) => s?.slice(0, 10) || "—";

export const terminal = (s?: string) =>
  !!s &&
  [
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    "turn_limit",
    "timed_out",
    "infrastructure_error",
  ].includes(s);

export type TokenKind = "plain" | "kw" | "str" | "num" | "com" | "fn" | "key";
export type CodeToken = { kind: TokenKind; text: string };

const PY_KEYWORDS = new Set([
  "def",
  "class",
  "return",
  "if",
  "elif",
  "else",
  "for",
  "while",
  "in",
  "not",
  "and",
  "or",
  "is",
  "None",
  "True",
  "False",
  "import",
  "from",
  "as",
  "with",
  "try",
  "except",
  "finally",
  "raise",
  "pass",
  "break",
  "continue",
  "yield",
  "lambda",
  "async",
  "await",
  "assert",
]);

function extOf(filename?: string) {
  if (!filename) return "";
  const idx = filename.lastIndexOf(".");
  return idx >= 0 ? filename.slice(idx + 1).toLowerCase() : "";
}

export function highlightLine(line: string, filename?: string): CodeToken[] {
  const ext = extOf(filename);
  if (!line) return [{ kind: "plain", text: " " }];

  if (ext === "md") {
    if (/^\s*#{1,6}\s/.test(line)) {
      return [{ kind: "kw", text: line }];
    }
    if (/^\s*[-*+]\s/.test(line)) {
      const m = line.match(/^(\s*[-*+]\s)(.*)$/);
      if (m) {
        return [
          { kind: "num", text: m[1] },
          { kind: "plain", text: m[2] },
        ];
      }
    }
    return [{ kind: "plain", text: line }];
  }

  if (ext === "yaml" || ext === "yml" || ext === "toml") {
    if (/^\s*#/.test(line)) return [{ kind: "com", text: line }];
    const m = line.match(/^(\s*[\w.-]+)(\s*[:=])(.*)$/);
    if (m) {
      return [
        { kind: "key", text: m[1] },
        { kind: "plain", text: m[2] },
        { kind: "str", text: m[3] },
      ];
    }
    return [{ kind: "plain", text: line }];
  }

  if (ext === "json") {
    const tokens: CodeToken[] = [];
    const re =
      /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?/g;
    let last = 0;
    let match: RegExpExecArray | null;
    while ((match = re.exec(line)) !== null) {
      if (match.index > last) {
        tokens.push({ kind: "plain", text: line.slice(last, match.index) });
      }
      if (match[1]) {
        tokens.push({ kind: match[2] ? "key" : "str", text: match[1] });
        if (match[2]) tokens.push({ kind: "plain", text: match[2] });
      } else if (match[3]) {
        tokens.push({ kind: "kw", text: match[3] });
      } else {
        tokens.push({ kind: "num", text: match[0] });
      }
      last = re.lastIndex;
    }
    if (last < line.length) {
      tokens.push({ kind: "plain", text: line.slice(last) });
    }
    return tokens;
  }

  if (ext === "py") {
    const tokens: CodeToken[] = [];
    const re =
      /#.*$|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b\d+(?:\.\d+)?\b|\b[A-Za-z_]\w*\b/g;
    let last = 0;
    let prevKw = false;
    let match: RegExpExecArray | null;
    while ((match = re.exec(line)) !== null) {
      if (match.index > last) {
        tokens.push({ kind: "plain", text: line.slice(last, match.index) });
      }
      const val = match[0];
      if (val.startsWith("#")) {
        tokens.push({ kind: "com", text: val });
      } else if (val.startsWith('"') || val.startsWith("'")) {
        tokens.push({ kind: "str", text: val });
        prevKw = false;
      } else if (/^\d/.test(val)) {
        tokens.push({ kind: "num", text: val });
        prevKw = false;
      } else if (PY_KEYWORDS.has(val)) {
        tokens.push({ kind: "kw", text: val });
        prevKw = val === "def" || val === "class";
      } else if (prevKw) {
        tokens.push({ kind: "fn", text: val });
        prevKw = false;
      } else {
        tokens.push({ kind: "plain", text: val });
      }
      last = re.lastIndex;
    }
    if (last < line.length) {
      tokens.push({ kind: "plain", text: line.slice(last) });
    }
    return tokens;
  }

  return [{ kind: "plain", text: line }];
}
