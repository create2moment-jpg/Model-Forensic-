import { Route, Routes } from "react-router-dom";
import { CheckPage } from "./pages/Check";
import { StudyWizardPage } from "./pages/StudyWizard";
import { Layout } from "./components/Layout";
import { AiPage } from "./pages/Ai";
import { ComparePage } from "./pages/Compare";
import { DiagnosticsPage } from "./pages/Diagnostics";
import { HomePage } from "./pages/Home";
import { LibraryPage } from "./pages/Library";
import { KnowledgePage } from "./pages/Knowledge";
import { ProgressionPage } from "./pages/Progression";
import { ReportsPage } from "./pages/Reports";
import { RunDetailPage } from "./pages/RunDetail";
import { RunsPage } from "./pages/Runs";
import { SettingsPage } from "./pages/Settings";
import { StudiesPage } from "./pages/Studies";
import { StudyPage } from "./pages/Study";
import { Empty } from "./components/ui";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<HomePage />} />
        <Route path="check" element={<CheckPage />} />
        <Route path="study-wizard" element={<StudyWizardPage />} />
        <Route path="studies" element={<StudiesPage />} />
        <Route path="studies/:studyId" element={<StudyPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="runs/:runId" element={<RunDetailPage />} />
        <Route path="compare" element={<ComparePage />} />
        <Route path="progression" element={<ProgressionPage />} />
        <Route path="ai" element={<AiPage />} />
        <Route path="library" element={<LibraryPage />} />
        <Route path="knowledge" element={<KnowledgePage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="diagnostics" element={<DiagnosticsPage />} />
        <Route path="*" element={<div className="page"><Empty title="Page not found" /></div>} />
      </Route>
    </Routes>
  );
}
