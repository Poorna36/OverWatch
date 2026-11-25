import { useState } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Sidebar } from './layout/Sidebar'
import { HomePage } from './pages/HomePage'
import { NewTaskPage } from './pages/NewTaskPage'
import { PlanPreviewPage } from './pages/PlanPreviewPage'
import { SessionPage } from './pages/SessionPage'
import { AuditPage } from './pages/AuditPage'
import { SettingsPage } from './pages/SettingsPage'
import { LiveScenePage } from './pages/LiveScenePage'
import { MobileBridgePopup } from './components/MobileBridgePopup'
import './index.css'

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(true)

  return (
    <BrowserRouter>
      <div className="flex h-full bg-[#141413] relative">
        {sidebarOpen ? (
          <Sidebar onCollapse={() => setSidebarOpen(false)} />
        ) : (
          <button
            onClick={() => setSidebarOpen(true)}
            title="Show sidebar"
            className="fixed top-3 left-3 z-50 flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-[#45423C] bg-[#1C1B19]/95 text-xs text-[#B0AAA0] hover:text-[#F4F1EA] hover:border-[#D97757] shadow-lg transition-all"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </svg>
            <span className="font-medium">Menu</span>
          </button>
        )}

        <main className="flex-1 flex flex-col overflow-hidden">
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/live-scene" element={<LiveScenePage />} />
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
