import { useQuery } from "@tanstack/react-query";
import { Activity, BookMarked, CloudDownload, Bot, HeartPulse, ChevronsLeft, ChevronsRight, FileText, GitCompare, Home, Layers, ListChecks, Moon, Microscope, Search, Settings, Sun, TrendingDown } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useHotkey, useLocalStorage } from "../lib/hooks";
import { LlmBadge } from "./LlmBadge";
import { TaskIndicator } from "./TaskIndicator";
import { useApp } from "./AppState";
import { CommandPalette } from "./CommandPalette";
import { JobTray } from "./JobTray";
import { Tour } from "./Tour";
import { ProjectSelector } from "./ProjectSelector";
import { WorkspaceSelector } from "./WorkspaceSelector";

type Mode = "simple" | "standard" | "expert";
// simple = beginner: only the guided pages; advanced pages appear from "standard" on.
const SIMPLE = new Set(["/check", "/", "/runs"]);
const NAV: { to: string; label: string; icon: ReactNode; kbd?: string }[] = [
  { to: "/check", label: "Check my run", icon: <HeartPulse size={16} />, kbd: "g k" },
  { to: "/", label: "Command center", icon: <Home size={16} /> },
  { to: "/studies", label: "Studies", icon: <Microscope size={16} />, kbd: "g t" },
  { to: "/runs", label: "Runs", icon: <Search size={16} />, kbd: "g r" },
  { to: "/progression", label: "Progression", icon: <TrendingDown size={16} />, kbd: "g p" },
  { to: "/compare", label: "Compare", icon: <GitCompare size={16} />, kbd: "g c" },
  { to: "/ai", label: "AI investigation", icon: <Bot size={16} />, kbd: "g a" },
];
const NAV2: { to: string; label: string; icon: ReactNode }[] = [
  { to: "/knowledge", label: "Knowledge", icon: <BookMarked size={16} /> },
  { to: "/prefetch", label: "Prefetch", icon: <CloudDownload size={16} /> },
  { to: "/library", label: "Library", icon: <Layers size={16} /> },
  { to: "/reports", label: "Reports", icon: <FileText size={16} /> },
];

export function Layout() {
  const [theme, setTheme] = useLocalStorage<"dark" | "light">("mf.theme", "dark");
  const [collapsed, setCollapsed] = useLocalStorage("mf.sidebar.collapsed", false);
  const { setPaletteOpen, setTrayOpen, trayOpen, jobs } = useApp();
  const nav = useNavigate();
  const loc = useLocation();
  const [mode, setMode] = useLocalStorage<Mode>("mf.mode", "standard");
  const visible = (to: string): boolean => mode !== "simple" || SIMPLE.has(to);
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, staleTime: 30_000 });
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, staleTime: 300_000, retry: false });
  document.documentElement.dataset.theme = theme;
  const toggleTheme = (): void => setTheme(theme === "dark" ? "light" : "dark");

  useHotkey("mod+k", () => setPaletteOpen(true));
  useHotkey("/", () => setPaletteOpen(true));
  useHotkey("g r", () => nav("/runs"));
  useHotkey("g k", () => nav("/check"));
  useHotkey("g c", () => nav("/compare"));
  useHotkey("g t", () => nav("/studies"));
  useHotkey("g p", () => nav("/progression"));
  useHotkey("g a", () => nav("/ai"));
  useHotkey("g h", () => nav("/"));
  useHotkey("g s", () => nav("/settings"));

  const running = jobs.filter((j) => j.status === "running").length;
  const crumbs = loc.pathname.split("/").filter(Boolean).map(decodeURIComponent);

  return (
    <div className={`shell ${collapsed ? "collapsed" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-logo"><Search size={15} strokeWidth={2.6} /></div>
          <span className="brand-name">Model Forensic</span>
        </div>
        <div className="mode-switch" title="Simple hides advanced pages; expert shows everything">
          {(["simple", "standard", "expert"] as Mode[]).map((m) => (
            <button key={m} className={`btn sm ${mode === m ? "primary" : "ghost"}`} onClick={() => setMode(m)}>{m}</button>
          ))}
        </div>
        {NAV.filter((n) => visible(n.to)).map((n) => (
          <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`} title={n.label}>
            {n.icon}<span className="nav-label">{n.label}</span>{n.kbd && <span className="kbd">{n.kbd}</span>}
          </NavLink>
        ))}
        {mode !== "simple" && <div className="nav-section">Workbench</div>}
        {NAV2.filter((n) => visible(n.to)).map((n) => (
          <NavLink key={n.to} to={n.to} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`} title={n.label}>
            {n.icon}<span className="nav-label">{n.label}</span>
          </NavLink>
        ))}
        <div className={`nav-item ${trayOpen ? "active" : ""}`} onClick={() => setTrayOpen(!trayOpen)} title="Jobs">
          <ListChecks size={16} /><span className="nav-label">Jobs</span>
          {running > 0 && <span className="pill accent running" style={{ marginLeft: "auto" }}><span className="dot" />{running}</span>}
        </div>
        <div className="sidebar-foot">
          <NavLink to="/diagnostics" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`} title="Diagnostics">
            <Activity size={16} /><span className="nav-label">Diagnostics</span>
          </NavLink>
          <NavLink to="/settings" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`} title="Settings">
            <Settings size={16} /><span className="nav-label">Settings</span>
          </NavLink>
          <div className="nav-item" onClick={toggleTheme} title="Toggle theme">
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}<span className="nav-label">{theme === "dark" ? "Light" : "Dark"} theme</span>
          </div>
          <div className="nav-item" onClick={() => setCollapsed(!collapsed)} title="Collapse sidebar">
            {collapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}<span className="nav-label">Collapse</span>
          </div>
          {health.data && (
            <div className="nav-label faint" style={{ fontSize: 11, padding: "8px 10px 0" }}>
              {me.data?.mode === "shared" && <div title={me.data.admin ? "admin" : "member"}>{me.data.name}{me.data.admin ? " · admin" : ""}</div>}
              v{health.data.version} · {health.data.backends.join(", ")}{health.data.read_only ? " · read-only" : ""}
            </div>
          )}
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <ProjectSelector />
          <WorkspaceSelector />
          <div className="crumbs">
            {crumbs.map((c, i) => (<span key={i} className="row" style={{ gap: 6, minWidth: 0 }}>{i > 0 && <span className="faint">/</span>}{i === crumbs.length - 1 ? <b>{c}</b> : <span>{c}</span>}</span>))}
          </div>
          <div className="search-trigger" onClick={() => setPaletteOpen(true)}>
            <Search size={14} /> Search runs or run a command… <span className="kbd">⌘K</span>
          </div>
          <TaskIndicator />
          <LlmBadge />
        </header>
        <div className="content"><Outlet /></div>
        <Tour />
      </div>
      <CommandPalette toggleTheme={toggleTheme} />
      <JobTray />
    </div>
  );
}
