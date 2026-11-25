import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Sidebar } from './layout/Sidebar'
import { HomePage } from './pages/HomePage'
import { NewTaskPage } from './pages/NewTaskPage'
import { PlanPreviewPage } from './pages/PlanPreviewPage'
import { SessionPage } from './pages/SessionPage'
import { AuditPage } from './pages/AuditPage'
import { SettingsPage } from './pages/SettingsPage'
import { MobileBridgePopup } from './components/MobileBridgePopup'
import './index.css'

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-full bg-[#141413]">
        <Sidebar />
        <main className="flex-1 flex flex-col overflow-hidden">
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/new" element={<NewTaskPage />} />
            <Route path="/plan" element={<PlanPreviewPage />} />
            <Route path="/session/:id" element={<SessionPage />} />
            <Route path="/audit" element={<AuditPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>
        <MobileBridgePopup />
      </div>
    </BrowserRouter>
  )
}
