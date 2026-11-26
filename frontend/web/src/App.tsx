import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { StoreProvider } from "./lib/store";
import { ToastHost } from "./lib/toast";
import { HomePage } from "./pages/HomePage";
import { NewTaskPage } from "./pages/NewTaskPage";
import { PlanPreviewPage } from "./pages/PlanPreviewPage";
import { SessionPage } from "./pages/SessionPage";
import { AuditPage } from "./pages/AuditPage";
import { SettingsPage } from "./pages/SettingsPage";

export default function App() {
  return (
    <BrowserRouter>
      <StoreProvider>
        <Shell>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/new" element={<NewTaskPage />} />
            <Route path="/plan" element={<PlanPreviewPage />} />
            <Route path="/session/:id" element={<SessionPage />} />
            <Route path="/audit" element={<AuditPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Shell>
        <ToastHost />
      </StoreProvider>
    </BrowserRouter>
  );
}
