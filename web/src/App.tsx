import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { AiPage } from "./pages/Ai";
import { ComparePage } from "./pages/Compare";
import { DiagnosticsPage } from "./pages/Diagnostics";
import { HomePage } from "./pages/Home";
import { LibraryPage } from "./pages/Library";
import { ProgressionPage } from "./pages/Progression";
import { ReportsPage } from "./pages/Reports";
import { RunDetailPage } from "./pages/RunDetail";
import { RunsPage } from "./pages/Runs";
import { SettingsPage } from "./pages/Settings";
import { Empty } from "./components/ui";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<HomePage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="runs/:runId" element={<RunDetailPage />} />
        <Route path="compare" element={<ComparePage />} />
        <Route path="progression" element={<ProgressionPage />} />
        <Route path="ai" element={<AiPage />} />
        <Route path="library" element={<LibraryPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="diagnostics" element={<DiagnosticsPage />} />
        <Route path="*" element={<div className="page"><Empty title="Page not found" /></div>} />
      </Route>
    </Routes>
  );
}
