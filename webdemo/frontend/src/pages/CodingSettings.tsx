import { useEffect, useState } from "react";
import { useI18n } from "../i18n/context";
import { api, useData } from "../lib/api";
import { sessionPath } from "../lib/coding";

type Kind = "skills" | "mcp" | "hooks";
type Bindings = Record<Kind, string[]>;
type Profile = {
  profile_id: string;
  provider: string;
  model: string;
  source: string;
  protocol: string;
  revision: number;
  configured: boolean;
  endpoint?: string;
  permission_mode?: string;
  max_iterations?: number;
  command_timeout?: number;
  context_window?: number;
};
type Options = {
  profile_id: string;
  catalog: Bindings;
  selected: Bindings;
  errors: string[];
  connections: Record<
    string,
    { tools: string[]; status: string; error?: string }
  >;
};
const empty: Bindings = { skills: [], mcp: [], hooks: [] };
export function CodingSettings({ sid }: { sid: string }) {
  const { locale } = useI18n();
  const word = (zh: string, en: string) => (locale === "zh" ? zh : en);
  const [rev, setRev] = useState(0);
  const profiles = useData<Profile[]>("coding/profiles", rev);
  const [connectionRev, refreshConnections] = useState(0);
  const options = useData<Options>(
    sid ? sessionPath(sid) + "/settings" : null,
    connectionRev,
  );
  const [selected, select] = useState(
    localStorage.getItem("coding.profile") || "",
  );
  const [bindings, bind] = useState(empty);
  const [editor, setEditor] = useState<Profile | null>(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [discovered, list] = useState<Record<string, string[]>>({});
  useEffect(() => {
    if (options.data) {
      select(options.data.profile_id);
      bind(options.data.selected);
    }
  }, [options.data]);
  async function execute(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setStatus("");
    try {
      await action();
      setStatus(word("已保存", "Saved"));
    } catch (err) {
      setStatus((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const edit = profiles.data?.find(
    (p) => p.profile_id === selected && p.source === "web",
  );
  return (
    <section className="coding-start coding-settings">
      <h1>{word("Coding 设置", "Coding settings")}</h1>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void execute(async () => {
            if (sid)
              await api(sessionPath(sid) + "/settings", {
                profile_id: selected,
                ...bindings,
              });
            else localStorage.setItem("coding.profile", selected);
            setRev((x) => x + 1);
          });
        }}
      >
        <label>
          {word("下一轮使用的模型", "Model for the next turn")}
          <select value={selected} onChange={(e) => select(e.target.value)}>
            <option value="">{word("本机默认配置", "Local default")}</option>
            {profiles.data?.map((p) => (
              <option key={p.profile_id} value={p.profile_id}>
                {p.provider} · {p.model} · {p.source}{" "}
                {p.revision ? "v" + p.revision : ""}{" "}
                {p.configured ? "••••" : word("缺少凭据", "Missing credential")}
              </option>
            ))}
          </select>
        </label>
        {options.data &&
          (["skills", "mcp", "hooks"] as Kind[]).map((kind) => (
            <fieldset key={kind}>
              <legend>
                {{ skills: "Skills", mcp: "MCP", hooks: "Hooks" }[kind]}
              </legend>
              {options.data!.catalog[kind].length === 0 && (
                <small>{word("未发现可用项", "None available")}</small>
              )}
              {options.data!.catalog[kind].map((name) => (
                <label key={name}>
                  <input
                    type="checkbox"
                    checked={bindings[kind].includes(name)}
                    disabled={busy}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      void execute(async () => {
                        if (kind === "mcp" && checked) {
                          const data = await api<{ tools: string[] }>(
                            sessionPath(sid) +
                              "/mcp/" +
                              encodeURIComponent(name) +
                              "/connect",
                            {},
                          );
                          list((prev) => ({ ...prev, [name]: data.tools }));
                        }
                        const next = {
                          ...bindings,
                          [kind]: checked
                            ? [...bindings[kind], name]
                            : bindings[kind].filter((n) => n !== name),
                        };
                        await api(sessionPath(sid) + "/settings", {
                          profile_id: selected,
                          ...next,
                        });
                        bind(next);
                        refreshConnections((n) => n + 1);
                      });
                    }}
                  />
                  {name}
                  {kind === "mcp" && bindings.mcp.includes(name) && (
                    <small>
                      {(
                        discovered[name] ||
                        options.data?.connections[name]?.tools ||
                        []
                      ).join(", ")}
                    </small>
                  )}
                </label>
              ))}
            </fieldset>
          ))}
        <button disabled={busy}>{word("应用配置", "Apply settings")}</button>
      </form>
      <div className="coding-actions">
        <button
          onClick={() =>
            setEditor({
              profile_id: "",
              provider: "",
              model: "",
              protocol: "openai-compat",
              source: "web",
              configured: false,
              revision: 0,
            })
          }
        >
          {word("新增 Provider", "New provider")}
        </button>
        {edit && (
          <>
            <button onClick={() => setEditor(edit)}>
              {word("编辑配置", "Edit profile")}
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void execute(async () => {
                  await api(
                    "coding/profiles/" + edit.profile_id + "/revoke",
                    {},
                  );
                  setRev((x) => x + 1);
                })
              }
            >
              {word("撤销凭据", "Revoke credential")}
            </button>
          </>
        )}
      </div>
      {editor && (
        <form
          key={editor.profile_id}
          onSubmit={(e) => {
            e.preventDefault();
            const fields = Object.fromEntries(new FormData(e.currentTarget));
            void execute(async () => {
              const p = await api<{ profile_id: string }>("coding/profiles", {
                ...fields,
                revision: editor.revision,
                profile_id: editor.profile_id,
              });
              select(p.profile_id);
              setEditor(null);
              setRev((x) => x + 1);
            });
          }}
        >
          {(
            [
              ["provider", "Provider"],
              ["endpoint", word("端点", "Endpoint")],
              ["model", word("模型", "Model")],
            ] as const
          ).map(([key, label]) => (
            <label key={key}>
              {label}
              <input required name={key} defaultValue={editor[key] || ""} />
            </label>
          ))}
          <label>
            {word("协议", "Protocol")}
            <select name="protocol" defaultValue={editor.protocol}>
              {["openai-compat", "openai", "anthropic"].map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </label>
          <label>
            {word("凭据", "API key")}
            <input
              type="password"
              name="api_key"
              autoComplete="new-password"
              required={!editor.configured}
              placeholder={editor.configured ? "••••" : ""}
            />
          </label>
          <label>
            {word("权限模式", "Permissions")}
            <select
              name="permission_mode"
              defaultValue={editor.permission_mode || "default"}
            >
              {["default", "acceptEdits", "plan", "bypassPermissions"].map(
                (p) => (
                  <option key={p}>{p}</option>
                ),
              )}
            </select>
          </label>
          {(
            [
              [
                "command_timeout",
                word("命令时限（秒）", "Command timeout (s)"),
                3600,
              ],
              ["max_iterations", word("迭代上限", "Maximum iterations"), 40],
              ["context_window", word("上下文窗口", "Context window"), 128000],
            ] as const
          ).map(([key, label, fallback]) => (
            <label key={key}>
              {label}
              <input
                type="number"
                name={key}
                min="1"
                required
                defaultValue={editor[key] ?? fallback}
              />
            </label>
          ))}
          <button disabled={busy}>
            {word("保存 Provider", "Save provider")}
          </button>
          <button type="button" onClick={() => setEditor(null)}>
            {word("关闭", "Close")}
          </button>
        </form>
      )}
      <p role="status">
        {[
          status,
          profiles.error,
          options.error,
          ...(options.data?.errors || []),
          ...Object.values(options.data?.connections || {})
            .filter((c) => c.status === "failed")
            .map((c) => c.error),
        ]
          .filter(Boolean)
          .join(" · ")}
      </p>
      <a href={"#coding" + (sid ? "/" + sid : "")}>Coding Workspace</a>
    </section>
  );
}
