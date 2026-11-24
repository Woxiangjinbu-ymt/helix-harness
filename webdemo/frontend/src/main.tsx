import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ChevronRight,
  FlaskConical,
  FolderGit2,
  GitBranch,
  Globe,
  Layers3,
  LayoutDashboard,
  Moon,
  Plus,
  Search,
  Sun,
  Terminal,
} from "lucide-react";
import { I18nProvider, useI18n } from "./i18n/context";
import { ThemeProvider, useTheme } from "./theme/context";
import { go, useData, useRoute } from "./lib/api";
import { num, relativeTime, short } from "./lib/format";
import { WorkspaceProvider, useWorkspace } from "./lib/workspaceContext";
import { Dashboard } from "./pages/Dashboard";
import { Workspace } from "./pages/Workspace";
const CodingWorkspace = lazy(() => import("./pages/CodingWorkspace").then(m => ({default:m.CodingWorkspace})));
import { Evolution } from "./pages/Evolution";
import { Evaluations } from "./pages/Evaluations";
import { Versions } from "./pages/Versions";
import type { Repo, Task } from "./types";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/components.css";
import "./styles/pages.css";

function AppShell() {
  const [route = "overview", id] = useRoute();
  const { t, locale, toggleLocale } = useI18n();
  const { theme, toggleTheme } = useTheme();
  const { repo, setRepo, rev, setRegisterOpen } = useWorkspace();

  const [sessionFilter, setSessionFilter] = useState("");
  const [showFilterInput, setShowFilterInput] = useState(false);
  const [expandedRepos, setExpandedRepos] = useState<Record<string, boolean>>({});

  const health = useData<{ release_verified: boolean; sandbox_ready: boolean }>(
    "health",
  );
  const repos = useData<Repo[]>("repositories", rev);
  const tasks = useData<Task[]>("tasks", rev);

  const ready = health.data?.release_verified && health.data.sandbox_ready;
  const status = health.error
    ? t.health.apiUnavailable
    : !health.data
      ? t.health.checking
      : !health.data.release_verified
        ? t.health.releaseFailed
        : !health.data.sandbox_ready
          ? t.health.sandboxUnavailable
          : t.health.online;

  const links = [
    { id: "overview", name: t.nav.overview, icon: LayoutDashboard },
    { id: "evolution", name: t.nav.evolution, icon: GitBranch },
    { id: "evaluations", name: t.nav.evaluations, icon: FlaskConical },
    { id: "versions", name: t.nav.versions, icon: Layers3 },
  ];

  const allNavNames: Record<string, string> = {
    overview: t.nav.overview,
    workspace: t.nav.workspace,
    evolution: t.nav.evolution,
    evaluations: t.nav.evaluations,
    versions: t.nav.versions,
  };

  const activeRoute = allNavNames[route] ? route : "overview";
  const isIDE = activeRoute === "workspace";

  // Group tasks by repository (like Antigravity / Codex "Projects" tree)
  const projectGroups = useMemo(() => {
    const q = sessionFilter.trim().toLowerCase();
    const repoList = repos.data || [];
    const taskList = tasks.data || [];

    const byRepo = new Map<string, Task[]>();
    for (const r of repoList) {
      byRepo.set(r.id, []);
    }
    for (const item of taskList) {
      if (
        q &&
        !item.case.toLowerCase().includes(q) &&
        !(item.model || "").toLowerCase().includes(q) &&
        !(item.session || "").toLowerCase().includes(q)
      ) {
        continue;
      }
      if (!byRepo.has(item.case)) {
        byRepo.set(item.case, []);
      }
      byRepo.get(item.case)!.push(item);
    }

    return [...byRepo.entries()].map(([repoId, items]) => {
      const meta = repoList.find((r) => r.id === repoId);
      return {
        id: repoId,
        name: meta?.name || repoId,
        kind: meta?.kind || "Archive",
        items,
      };
    });
  }, [repos.data, tasks.data, sessionFilter]);

  const activeTask = useMemo(
    () =>
      isIDE && id
        ? (tasks.data || []).find((x) => (x.job || x.id) === id)
        : undefined,
    [isIDE, id, tasks.data],
  );

  return (
    <div className="app">
      <aside className="app-sidebar">
        <div className="brand">
          <img
            src="/helix.svg"
            alt="Helix"
            className="brand-mark"
            width="28"
            height="28"
          />
          <div className="brand-title">
            Helix
            <small>{t.brand.subtitle}</small>
          </div>
        </div>

        <button
          type="button"
          className={
            "sidebar-new-btn" + (isIDE && !id ? " active" : "")
          }
          onClick={() => go("workspace")}
        >
          <Plus size={15} />
          <span>{t.workspace.newTask}</span>
        </button>

        <nav className="app-nav">
          <a href="#coding"><Terminal size={15} />Coding Workspace</a>
          {links.map(({ id: linkId, name, icon: Icon }) => (
            <a
              key={linkId}
              className={activeRoute === linkId ? "selected" : ""}
              href={"#" + linkId}
            >
              <Icon size={15} />
              <span>{name}</span>
            </a>
          ))}
        </nav>

        {/* Classic Agent App Projects & Sessions Tree */}
        <div className="sidebar-projects-head">
          <span>{t.brand.projectsGroup}</span>
          <div className="row" style={{ gap: 2 }}>
            <button
              type="button"
              className="iconbtn"
              aria-label={t.workspace.searchSessions}
              title={t.workspace.searchSessions}
              onClick={() => {
                setShowFilterInput((v) => !v);
                if (showFilterInput) setSessionFilter("");
              }}
            >
              <Search size={13} />
            </button>
            <button
              type="button"
              className="iconbtn"
              aria-label={t.workspace.registerRepository}
              title={t.workspace.registerRepository}
              onClick={() => {
                if (!isIDE) go("workspace");
                setRegisterOpen(true);
              }}
            >
              <Plus size={14} />
            </button>
          </div>
        </div>

        {showFilterInput && (
          <div className="sidebar-search-wrap">
            <input
              autoFocus
              type="text"
              value={sessionFilter}
              onChange={(e) => setSessionFilter(e.target.value)}
              placeholder={t.workspace.searchSessions}
              aria-label={t.workspace.searchSessions}
            />
          </div>
        )}

        <div className="sidebar-projects">
          {projectGroups.map((group) => {
            const expanded = !!expandedRepos[group.id];
            const visibleItems = expanded ? group.items : group.items.slice(0, 4);
            const remaining = group.items.length - visibleItems.length;
            const isSelectedRepo = isIDE && !id && repo === group.id;

            return (
              <div className="project-group" key={group.id}>
                <button
                  type="button"
                  className={
                    "project-group-head" + (isSelectedRepo ? " selected" : "")
                  }
                  onClick={() => {
                    setRepo(group.id);
                    go("workspace");
                  }}
                >
                  <FolderGit2 size={13} />
                  <span className="project-group-name">{group.name}</span>
                  {group.items.length > 0 && (
                    <span className="project-group-count">
                      {group.items.length}
                    </span>
                  )}
                </button>

                {visibleItems.length > 0 ? (
                  <div className="project-sessions">
                    {visibleItems.map((item) => {
                      const targetId = item.job || item.id;
                      const selected = isIDE && id === targetId;
                      return (
                        <button
                          type="button"
                          key={item.id}
                          className={
                            "project-session-row" + (selected ? " selected" : "")
                          }
                          onClick={() => {
                            setRepo(item.case);
                            go("workspace", targetId);
                          }}
                        >
                          <span className="project-session-label">
                            {item.model || short(item.session || item.id)}
                          </span>
                          <span className="project-session-right">
                            {item.score != null && (
                              <span
                                className={
                                  "project-score-dot" +
                                  (item.score === 100 ? " perfect" : "")
                                }
                              >
                                {num(item.score)}
                              </span>
                            )}
                            <span className="project-session-time">
                              {relativeTime(item.date, locale)}
                            </span>
                          </span>
                        </button>
                      );
                    })}
                    {remaining > 0 && (
                      <button
                        type="button"
                        className="project-more-btn"
                        onClick={() =>
                          setExpandedRepos((prev) => ({
                            ...prev,
                            [group.id]: true,
                          }))
                        }
                      >
                        {t.workspace.moreSessions(remaining)}
                      </button>
                    )}
                  </div>
                ) : (
                  <div className="project-empty">{t.brand.noSessionsYet}</div>
                )}
              </div>
            );
          })}
        </div>

        <div className="asidefoot">
          <div className="aside-status">
            <i className={ready ? "online" : "unknown-dot"} />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="aside-status-title">{status}</div>
              <small>{t.brand.runtimeName}</small>
            </div>
          </div>
        </div>
      </aside>

      <main className="app-main">
        <header className="topbar">
          <div className="topbar-left">
            {isIDE ? (
              <>
                <Terminal size={14} style={{ color: "var(--text-muted)" }} />
                <span className="crumb">
                  {activeTask?.case || repo}
                </span>
                <ChevronRight
                  size={13}
                  style={{ color: "var(--text-faint)" }}
                />
                <span className="topbar-title">
                  {activeTask
                    ? `${activeTask.model || short(activeTask.session || activeTask.id)}`
                    : t.workspace.newTask}
                </span>
              </>
            ) : (
              <>
                <span className="crumb">{t.brand.crumbWorkspace}</span>
                <ChevronRight
                  size={13}
                  style={{ color: "var(--text-faint)" }}
                />
                <span className="topbar-title">{allNavNames[activeRoute]}</span>
              </>
            )}
          </div>
          <div className="topbar-right">
            <button
              type="button"
              className="topbar-btn"
              onClick={toggleLocale}
              aria-label={t.controls.switchLanguage}
              title={t.controls.switchLanguage}
            >
              <Globe size={13} />
              <span>{locale === "en" ? "中文" : "EN"}</span>
            </button>
            <button
              type="button"
              className="topbar-btn icon-only"
              onClick={toggleTheme}
              aria-label={
                theme === "dark"
                  ? t.controls.switchToLight
                  : t.controls.switchToDark
              }
              title={
                theme === "dark"
                  ? t.controls.switchToLight
                  : t.controls.switchToDark
              }
            >
              {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
            </button>
          </div>
        </header>

        <div className={"content" + (isIDE ? " content-ide" : "")}>
          {activeRoute === "overview" ? (
            <Dashboard />
          ) : activeRoute === "evaluations" ? (
            <Evaluations />
          ) : activeRoute === "versions" ? (
            <Versions id={id} />
          ) : activeRoute === "workspace" ? (
            <Workspace id={id} />
          ) : (
            <Evolution id={id} />
          )}
        </div>
      </main>
    </div>
  );
}

function App() {
  const [route, id] = useRoute();
  const coding = !route || route === "coding" || route === "coding-settings";
  useEffect(() => { document.title = coding ? "MewCode · Coding Workspace" : "Helix · RSI Center"; }, [coding]);
  if (coding) {
    return <Suspense fallback={null}><CodingWorkspace key={route + "/" + (id || "")} id={id} settings={route === "coding-settings"} /></Suspense>;
  }
  return <WorkspaceProvider><AppShell /></WorkspaceProvider>;
}

createRoot(document.getElementById("root")!).render(
  <ThemeProvider>
    <I18nProvider>
      <App />
    </I18nProvider>
  </ThemeProvider>,
);
