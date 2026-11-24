import { useEffect, useState } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  Check,
  Copy,
  LoaderCircle,
  Terminal,
  X,
} from "lucide-react";
import type { ReactNode } from "react";
import { useI18n } from "../i18n/context";

export function Badge({ value }: { value: string }) {
  return <span className={"badge " + value.toLowerCase()}>{value}</span>;
}

export function Notice({
  error,
  children,
}: {
  error?: string;
  children?: ReactNode;
}) {
  const { t } = useI18n();
  return (
    <div className={"notice " + (error ? "error" : "")}>
      {error ? (
        <AlertCircle size={16} />
      ) : (
        <LoaderCircle size={16} className="spin" />
      )}
      <span>{error || children || t.controls.loading}</span>
    </div>
  );
}

export function Empty({
  icon,
  children,
}: {
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="empty">
      {icon || <Terminal size={22} />}
      <span>{children}</span>
    </div>
  );
}

export function Panel({
  title,
  icon,
  action,
  children,
  cls = "",
}: {
  title: string;
  icon?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  cls?: string;
}) {
  return (
    <section className={"panel " + cls}>
      <header className="panelhead">
        <h2>
          {icon}
          {title}
        </h2>
        {action}
      </header>
      {children}
    </section>
  );
}

export function LinkButton({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button className="link" onClick={onClick}>
      {children}
      <ArrowUpRight size={13} />
    </button>
  );
}

export function CopyButton({
  text,
  compact = false,
}: {
  text: string;
  compact?: boolean;
}) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // ignore clipboard errors
    }
  }

  return (
    <button
      type="button"
      className={compact ? "iconbtn" : "sm ghost"}
      onClick={handleCopy}
      aria-label={copied ? t.controls.copied : t.controls.copy}
      title={copied ? t.controls.copied : t.controls.copy}
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
      {!compact && (copied ? t.controls.copied : t.controls.copy)}
    </button>
  );
}

export function ScoreRing({ score }: { score: number | null | undefined }) {
  const size = 64;
  const stroke = 5;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = score == null ? 0 : Math.max(0, Math.min(100, score));
  const offset = c - (pct / 100) * c;
  const color =
    score == null
      ? "var(--border-strong)"
      : score >= 100
        ? "var(--accent)"
        : score >= 50
          ? "var(--amber)"
          : "var(--danger)";

  return (
    <div className="score-ring-wrap" aria-hidden="true">
      <svg className="score-ring-svg" width={size} height={size}>
        <circle
          className="score-ring-track"
          cx={size / 2}
          cy={size / 2}
          r={r}
          strokeWidth={stroke}
        />
        <circle
          className="score-ring-fill"
          cx={size / 2}
          cy={size / 2}
          r={r}
          strokeWidth={stroke}
          stroke={color}
          strokeDasharray={c}
          strokeDashoffset={offset}
        />
      </svg>
      <span className="score-ring-label">
        {score == null ? "—" : Math.round(score) + "%"}
      </span>
    </div>
  );
}

export function Modal({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const { t } = useI18n();
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-card" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button
            type="button"
            className="iconbtn"
            aria-label={t.controls.close}
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
