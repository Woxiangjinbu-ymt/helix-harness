import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { RefObject, ReactNode } from "react";
import {
  AttachmentFile,
  AttachmentImage,
  AttachmentList,
  ComposerCard,
  ContextRing,
  MenuSelect,
  ModelSelect,
  SendButton,
  TooltipLayer,
} from "@prismshadow/penguin-ui";
import penguinStyles from "@prismshadow/penguin-ui/styles.css?url";
import { BookOpen, Paperclip, Shield, Sparkles } from "lucide-react";
import { useTheme } from "../theme/context";
import { useI18n } from "../i18n/context";
import { api, useData } from "../lib/api";
import { sessionPath } from "../lib/coding";
import type { Entry, Session } from "../lib/coding";
import {
  attachmentURL,
  discardFile,
  keepFile,
  loadFile,
} from "../lib/composer";
import type {
  Attachment,
  DraftFile,
  Preferences,
  Profile,
} from "../lib/composer";

export function CodingTheme() {
  const { theme } = useTheme();
  useLayoutEffect(() => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = penguinStyles;
    document.head.prepend(link);
    return () => link.remove();
  }, []);
  useLayoutEffect(() => {
    const root = document.documentElement;
    const previous = root.classList.contains("dark");
    root.classList.toggle("dark", theme === "dark");
    return () => {
      root.classList.toggle("dark", previous);
    };
  }, [theme]);
  return <TooltipLayer />;
}

function FileTile({
  file,
  sid,
  remove,
  removeLabel,
}: {
  file: DraftFile;
  sid?: string;
  remove?: () => void;
  removeLabel?: string;
}) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    if (!file.local || !file.mime.startsWith("image/")) return;
    let live = true;
    let url = "";
    loadFile(file.id)
      .then((f) => {
        if (live && f) {
          url = URL.createObjectURL(f);
          setSrc(url);
        }
      })
      .catch(() => {});
    return () => {
      live = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [file.id, file.local, file.mime]);
  const url = file.local ? src : sid ? attachmentURL(sid, file.id) : "";
  return file.mime.startsWith("image/") && url ? (
    <AttachmentImage
      src={url}
      alt={file.name}
      lazy={!remove}
      removeLabel={removeLabel}
      onRemove={remove}
    />
  ) : (
    <AttachmentFile
      name={file.name}
      size={Math.ceil(file.size / 1024) + " KiB"}
      removeLabel={removeLabel}
      onRemove={remove}
    />
  );
}

export function FileTiles({
  files,
  sid,
  onRemove,
}: {
  files: DraftFile[];
  sid?: string;
  onRemove?: (id: string) => void;
}) {
  const { locale } = useI18n();
  return (
    <AttachmentList>
      {files.map((f) => (
        <FileTile
          key={f.id}
          sid={sid}
          file={f}
          remove={onRemove ? () => onRemove(f.id) : undefined}
          removeLabel={
            onRemove
              ? (locale === "zh" ? "移除 " : "Remove ") + f.name
              : undefined
          }
        />
      ))}
    </AttachmentList>
  );
}

function Picker({
  title,
  close,
  children,
}: {
  title: string;
  close: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current!;
    d.showModal();
    return () => d.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="composer-dialog"
      aria-label={title}
      onCancel={close}
      onClick={(e) => {
        if (e.target === ref.current) close();
      }}
    >
      <div>
        <header>
          <strong>{title}</strong>
          <button type="button" aria-label="Close" onClick={close}>
            ×
          </button>
        </header>
        {children}
      </div>
    </dialog>
  );
}

type Props = {
  sid?: string;
  session?: Session;
  entries: Entry[];
  input: RefObject<HTMLTextAreaElement | null>;
  draft: string;
  changeDraft: (s: string) => void;
  send: () => void;
  cancel: () => void;
  active: boolean;
  disabled: boolean;
  busy: boolean;
  prefs: Preferences;
  changePrefs: (p: Preferences) => void;
  files: DraftFile[];
  changeFiles: (change: (old: DraftFile[]) => DraftFile[]) => void;
  chooseDirectory: (reference?: boolean) => void;
  fail: (s: string) => void;
  profiles?: Profile[];
  profilesError?: string;
};
export function CodingComposer(p: Props) {
  const { locale, t } = useI18n();
  const c = t.coding;
  const w = (zh: string, en: string) => (locale === "zh" ? zh : en);
  const [panel, setPanel] = useState<"model" | "context" | "project" | null>(
    null,
  );
  useEffect(() => {
    const key = "coding.draft." + p.sid + ".reference";
    if (p.sid && sessionStorage.getItem(key)) {
      sessionStorage.removeItem(key);
      setPanel("project");
    }
  }, [p.sid]);
  const [query, setQuery] = useState("");
  const upload = useRef<HTMLInputElement>(null);
  const selecting = useRef(false);
  const [staging, setStaging] = useState(false);
  const settings = useData<{
    catalog: { skills: string[] };
    selected: { skills: string[] };
  }>(p.sid ? sessionPath(p.sid) + "/settings" : null);
  const project = useData<string[]>(
    panel === "project" && p.sid
      ? sessionPath(p.sid) + "/files?query=" + encodeURIComponent(query)
      : null,
  );
  const baseId =
    (p.sid ? p.session?.profile_id : localStorage.getItem("coding.profile")) ||
    "";
  const base =
    p.profiles?.find((m) => m.profile_id === baseId) ||
    p.profiles?.find((m) => m.source === "native");
  const model =
    p.profiles?.find((m) => m.profile_id === p.prefs.model_profile_id) || base;
  const permission =
    p.prefs.permission_mode || base?.permission_mode || "default";
  const skills = p.prefs.skills || settings.data?.selected.skills || [];
  const level = p.prefs.thinking_level || "default";
  const levelLabel = (v: string) =>
    ({
      default: w("模型默认", "Model default"),
      off: w("关闭", "Off"),
      on: w("开启", "On"),
      low: w("低", "Low"),
      medium: w("中", "Medium"),
      high: w("高", "High"),
      max: w("最高", "Max"),
    })[v] || v;
  const permissionLabel = (v: string) =>
    ({
      default: w("默认审批", "Default approval"),
      plan: w("规划", "Plan"),
      acceptEdits: w("允许编辑", "Accept edits"),
      bypassPermissions: w("全部放行", "Bypass permissions"),
    })[v] || v;
  const reading = p.entries.filter((e) => e.kind === "context").at(-1)?.payload;
  const same =
    reading?.profile_id === model?.profile_id &&
    reading?.window === model?.context_window;
  const windowSize = model?.context_window;
  const used = reading?.used;
  const fraction =
    windowSize && used !== undefined ? used / windowSize : undefined;
  const context =
    w("上下文", "Context") +
    (fraction !== undefined
      ? " · " + Math.round(fraction * 100) + "%"
      : " · " + w("待测", "Unmeasured"));
  const totals = new Map(
    p.entries
      .filter((e) => e.kind === "usage")
      .map((e) => [e.turn_id, e.payload]),
  );
  const sessionTokens = [...totals.values()].reduce(
    (n, v) => n + (v.input_tokens || 0) + (v.output_tokens || 0),
    0,
  );
  async function add(files: File[]) {
    if (!files.length || selecting.current || p.busy) return;
    if (
      files.length + p.files.length > 8 ||
      files.some((f) => f.size > 4 * 1024 * 1024 || !f.size)
    ) {
      p.fail(
        w(
          "每次最多 8 个非空附件，每个最多 4 MiB",
          "Choose up to 8 nonempty files, each at most 4 MiB",
        ),
      );
      return;
    }
    selecting.current = true;
    setStaging(true);
    const kept: DraftFile[] = [];
    try {
      for (const f of files) kept.push(await keepFile(f));
      p.changeFiles((old) => [...old, ...kept]);
      p.fail("");
    } catch (e) {
      kept.forEach((f) => void discardFile(f.id));
      p.fail((e as Error).message);
    } finally {
      selecting.current = false;
      setStaging(false);
    }
  }
  async function attachProject(path: string) {
    if (!p.sid || selecting.current || p.files.length >= 8) return;
    selecting.current = true;
    setStaging(true);
    try {
      const f = await api<Attachment>(
        sessionPath(p.sid) + "/references/" + crypto.randomUUID(),
        { path },
      );
      p.changeFiles((old) => [...old, f]);
      setPanel(null);
      p.fail("");
    } catch (e) {
      setPanel(null);
      p.fail((e as Error).message);
    } finally {
      selecting.current = false;
      setStaging(false);
    }
  }
  const locked = p.busy || staging;
  const incompatibleImage =
    model?.vision === false &&
    p.files.some(
      (f) => f.mime.startsWith("image/") && f.mime !== "image/svg+xml",
    );
  return (
    <section
      className="penguin-composer"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) e.preventDefault();
      }}
      onDrop={(e) => {
        e.preventDefault();
        void add([...e.dataTransfer.files]);
      }}
    >
      <input
        type="file"
        hidden
        multiple
        ref={upload}
        onChange={(e) => {
          void add(Array.from(e.target.files || []));
          e.target.value = "";
        }}
      />
      <ComposerCard
        chips={
          incompatibleImage && (
            <p className="composer-notice" role="status">
              {w(
                "当前模型不支持图片。请选择视觉模型或移除图片。",
                "Choose a vision model or remove the image to send.",
              )}
            </p>
          )
        }
        attachments={
          p.files.length ? (
            <FileTiles
              files={p.files}
              sid={p.sid}
              onRemove={
                locked
                  ? undefined
                  : (id) => {
                      p.changeFiles((old) => old.filter((f) => f.id !== id));
                      void discardFile(id).catch(() => {});
                    }
              }
            />
          ) : undefined
        }
        textarea={{
          ref: p.input,
          "aria-label": c.draft,
          autoFocus: true,
          placeholder: c.prompt,
          value: p.draft,
          disabled: Boolean(p.sid) && !p.session,
          readOnly: p.busy,
          onChange: (e) => p.changeDraft(e.target.value),
          onPaste: (e) => {
            if (e.clipboardData.files.length) {
              e.preventDefault();
              void add([...e.clipboardData.files]);
            }
          },
          onKeyDown: (e) => {
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.altKey &&
              !e.nativeEvent.isComposing &&
              e.nativeEvent.keyCode !== 229
            ) {
              e.preventDefault();
              if (!locked && !incompatibleImage) p.send();
            }
          },
        }}
        tools={
          <>
            <MenuSelect
              trigger={{
                glyph: <Paperclip size={16} />,
                ariaLabel: w("添加附件", "Add attachments"),
                disabled: locked,
              }}
              options={[
                { value: "upload", label: w("上传文件", "Upload files") },
                {
                  value: "project",
                  label: w("引用项目文件", "Reference project file"),
                },
              ]}
              onChange={(v) => {
                if (v === "upload") upload.current?.click();
                else if (p.sid) setPanel("project");
                else p.chooseDirectory(true);
              }}
            />
            <MenuSelect
              trigger={{
                glyph: <Shield size={16} />,
                ariaLabel:
                  w("权限", "Permissions") +
                  " · " +
                  permissionLabel(permission),
                disabled: locked,
              }}
              title={w("工具权限", "Tool permissions")}
              value={permission}
              options={[
                "default",
                "plan",
                "acceptEdits",
                "bypassPermissions",
              ].map((v) => ({ value: v, label: permissionLabel(v) }))}
              onChange={(v) => p.changePrefs({ permission_mode: v })}
            />
            <MenuSelect
              trigger={{
                glyph: <BookOpen size={16} />,
                badge: skills.length,
                ariaLabel: w("技能", "Skills"),
                disabled: locked,
              }}
              title={w("会话技能", "Session skills")}
              value={skills}
              multiple
              keepOpen
              options={settings.data?.catalog.skills.map((v) => ({
                value: v,
                label: v,
              }))}
              note={
                settings.error ||
                (!p.sid
                  ? w("选择项目以加载技能", "Choose a project to load skills")
                  : !settings.data?.catalog.skills.length
                    ? w("当前项目没有可选技能", "No skills available")
                    : "")
              }
              footer={
                !p.sid ? (
                  <button onClick={() => p.chooseDirectory()}>
                    {c.choose}
                  </button>
                ) : undefined
              }
              onChange={(v) =>
                p.changePrefs({
                  skills: skills.includes(v)
                    ? skills.filter((x) => x !== v)
                    : [...skills, v],
                })
              }
            />
          </>
        }
        actions={
          <>
            <ContextRing
              label={context}
              ratio={fraction || 0}
              unknown={fraction === undefined}
              expanded={panel === "context"}
              onClick={() => setPanel("context")}
            />
            <MenuSelect
              align="right"
              trigger={{
                glyph: <Sparkles size={14} />,
                label: levelLabel(level),
                caret: true,
                ariaLabel: w("思考强度", "Thinking effort"),
                disabled: locked,
              }}
              title={w("思考强度", "Thinking effort")}
              value={level}
              options={(model?.thinking_levels || ["default"]).map((v) => ({
                value: v,
                label: levelLabel(v),
              }))}
              onChange={(v) => p.changePrefs({ thinking_level: v })}
            />
            <ModelSelect
              provider={model?.provider || null}
              label={model?.model || w("选择模型", "Select model")}
              ariaLabel={w("选择模型", "Select model")}
              tooltip={model?.model || w("选择模型", "Select model")}
              disabled={locked}
              expanded={panel === "model"}
              onClick={() => setPanel("model")}
            />
            <SendButton
              action={p.active ? "stop" : "send"}
              label={p.active ? w("取消轮次", "Cancel turn") : c.send}
              disabled={p.disabled || locked || incompatibleImage}
              onClick={p.active ? p.cancel : p.send}
            />
          </>
        }
      />
      {panel === "model" && (
        <Picker
          title={w("选择模型", "Select model")}
          close={() => setPanel(null)}
        >
          {p.profilesError && <p role="alert">{p.profilesError}</p>}
          {p.profiles?.map((m) => (
            <button
              key={m.profile_id}
              aria-pressed={model?.profile_id === m.profile_id}
              disabled={!m.configured}
              onClick={() => {
                p.changePrefs({
                  model_profile_id: m.profile_id,
                  thinking_level: "default",
                });
                setPanel(null);
              }}
            >
              <span>{m.model}</span>
              <small>
                {m.provider} · {m.source}
                {!m.configured
                  ? " · " + w("缺少凭据", "Missing credentials")
                  : ""}
              </small>
            </button>
          ))}
        </Picker>
      )}
      {panel === "context" && (
        <Picker title={context} close={() => setPanel(null)}>
          <p>
            {model?.model} · {used?.toLocaleString() || "—"} /{" "}
            {windowSize?.toLocaleString() || "—"} tokens
          </p>
          <p>
            {used === undefined
              ? w("首次调用后显示用量", "Available after the first call")
              : same && reading?.source === "measured"
                ? w("模型实测", "Measured by provider")
                : w("估算", "Estimated")}
          </p>
          <p>
            {w("窗口来源", "Window source")} ·{" "}
            {model?.window_source === "configured"
              ? w("配置值", "Configured")
              : model?.window_source === "demo"
                ? "Demo"
                : w("原生运行时", "Native runtime")}
          </p>
          <p>
            {w("自动压缩阈值", "Auto-compact threshold")} ·{" "}
            {model && model.compact_threshold <= 0
              ? w("已达阈值", "Already at threshold")
              : model?.compact_threshold?.toLocaleString() || "—"}
          </p>
          <p>
            {w("会话累计用量", "Session token usage")} ·{" "}
            {totals.size ? sessionTokens.toLocaleString() : "—"}
          </p>
        </Picker>
      )}
      {panel === "project" && (
        <Picker
          title={w("引用项目文件", "Reference project file")}
          close={() => setPanel(null)}
        >
          <input
            aria-label={c.search}
            placeholder={c.search}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {project.error && <p role="alert">{project.error}</p>}
          {project.data?.map((path) => (
            <button
              key={path}
              disabled={locked || p.files.length >= 8}
              onClick={() => void attachProject(path)}
            >
              {path}
            </button>
          ))}
          {project.data?.length === 0 && <p>{c.empty}</p>}
        </Picker>
      )}
    </section>
  );
}
