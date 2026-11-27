import { lazy, Suspense, type ReactNode } from "react";
import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { HomePage } from "./pages/Home";
import { Empty, Loading } from "./components/ui";

/** Pages load when first opened (Today with the app): the first paint ships less JavaScript, and an update
 *  re-downloads only the pages that changed. */
const LocalRunsPage = lazy(() => import("./pages/LocalRuns").then((m) => ({ default: m.LocalRunsPage })));
const CheckPage = lazy(() => import("./pages/Check").then((m) => ({ default: m.CheckPage })));
const StudyWizardPage = lazy(() => import("./pages/StudyWizard").then((m) => ({ default: m.StudyWizardPage })));
const AiPage = lazy(() => import("./pages/Ai").then((m) => ({ default: m.AiPage })));
const ComparePage = lazy(() => import("./pages/Compare").then((m) => ({ default: m.ComparePage })));
const DiagnosticsPage = lazy(() => import("./pages/Diagnostics").then((m) => ({ default: m.DiagnosticsPage })));
const LibraryPage = lazy(() => import("./pages/Library").then((m) => ({ default: m.LibraryPage })));
const KnowledgePage = lazy(() => import("./pages/Knowledge").then((m) => ({ default: m.KnowledgePage })));
const PrefetchPage = lazy(() => import("./pages/Prefetch").then((m) => ({ default: m.PrefetchPage })));
const TestSpacePage = lazy(() => import("./pages/TestSpace").then((m) => ({ default: m.TestSpacePage })));
const ProgressionPage = lazy(() => import("./pages/Progression").then((m) => ({ default: m.ProgressionPage })));
const ReportsPage = lazy(() => import("./pages/Reports").then((m) => ({ default: m.ReportsPage })));
const RunDetailPage = lazy(() => import("./pages/RunDetail").then((m) => ({ default: m.RunDetailPage })));
const RunsPage = lazy(() => import("./pages/Runs").then((m) => ({ default: m.RunsPage })));
const SettingsPage = lazy(() => import("./pages/Settings").then((m) => ({ default: m.SettingsPage })));
const StudiesPage = lazy(() => import("./pages/Studies").then((m) => ({ default: m.StudiesPage })));
const StudyPage = lazy(() => import("./pages/Study").then((m) => ({ default: m.StudyPage })));
const LedgerPage = lazy(() => import("./pages/Ledger").then((m) => ({ default: m.LedgerPage })));

const page = (el: ReactNode): ReactNode => <Suspense fallback={<div className="page"><Loading label="Opening…" /></div>}>{el}</Suspense>;

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<HomePage />} />
        <Route path="check" element={page(<CheckPage />)} />
        <Route path="study-wizard" element={page(<StudyWizardPage />)} />
        <Route path="studies" element={page(<StudiesPage />)} />
        <Route path="studies/:studyId" element={page(<StudyPage />)} />
        <Route path="runs" element={page(<RunsPage />)} />
        <Route path="local" element={page(<LocalRunsPage />)} />
        <Route path="runs/:runId" element={page(<RunDetailPage />)} />
        <Route path="compare" element={page(<ComparePage />)} />
        <Route path="progression" element={page(<ProgressionPage />)} />
        <Route path="ai" element={page(<AiPage />)} />
        <Route path="library" element={page(<LibraryPage />)} />
        <Route path="knowledge" element={page(<KnowledgePage />)} />
        <Route path="prefetch" element={page(<PrefetchPage />)} />
        <Route path="test-space" element={page(<TestSpacePage />)} />
        <Route path="reports" element={page(<ReportsPage />)} />
        <Route path="ledger" element={page(<LedgerPage />)} />
        <Route path="settings" element={page(<SettingsPage />)} />
        <Route path="diagnostics" element={page(<DiagnosticsPage />)} />
        <Route path="*" element={<div className="page"><Empty title="Page not found" /></div>} />
      </Route>
    </Routes>
  );
}
