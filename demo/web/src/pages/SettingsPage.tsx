import { useState } from 'react'
import { Card, Button } from '../components/ui'

export function SettingsPage() {
  const [serverUrl, setServerUrl] = useState('http://localhost:8000')
  const [token, setToken] = useState('demo-token-hackathon-2025')
  const [googleConnected, setGoogleConnected] = useState(false)
  const [saved, setSaved] = useState(false)
  const [connecting, setConnecting] = useState(false)

  async function save() {
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  async function connectGoogle() {
    setConnecting(true)
    await new Promise(r => setTimeout(r, 1500))
    setGoogleConnected(true)
    setConnecting(false)
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-2xl mx-auto px-6 py-8 space-y-6">

        <div>
          <h1 className="text-2xl font-bold text-zinc-100">Settings</h1>
          <p className="text-sm text-zinc-500 mt-1">Configure your Overwatch connection</p>
        </div>

        {/* Server */}
        <section className="space-y-3">
          <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-widest">Control plane</h2>
          <Card>
            <div className="space-y-4">
              <div className="space-y-1.5">
                <label className="text-xs text-zinc-500">Server URL</label>
                <input
                  value={serverUrl}
                  onChange={e => setServerUrl(e.target.value)}
                  className="w-full rounded-lg bg-zinc-950 border border-zinc-800 px-3 py-2 text-sm font-mono text-zinc-200 focus:outline-none focus:border-indigo-500/60 transition-all"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs text-zinc-500">Bearer token</label>
                <input
                  type="password"
                  value={token}
                  onChange={e => setToken(e.target.value)}
                  className="w-full rounded-lg bg-zinc-950 border border-zinc-800 px-3 py-2 text-sm font-mono text-zinc-200 focus:outline-none focus:border-indigo-500/60 transition-all"
                />
              </div>
              <div className="flex items-center gap-2">
                <div className="flex items-center gap-1.5 text-xs text-emerald-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  Connected (demo mode)
                </div>
                <div className="flex-1" />
                <Button size="sm" onClick={save}>
                  {saved ? '✓ Saved' : 'Save'}
                </Button>
              </div>
            </div>
          </Card>
        </section>

        {/* Google connector (DEMO) */}
        <section className="space-y-3">
          <div className="flex items-center gap-2">
            <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-widest">Google connector</h2>
            <span className="text-[10px] font-medium text-indigo-400 bg-indigo-400/10 border border-indigo-400/20 rounded px-1.5 py-0.5">DEMO</span>
          </div>
          <Card>
            <div className="flex items-center gap-4">
              {/* Google logo */}
              <div className="w-10 h-10 rounded-xl bg-zinc-800 border border-zinc-700 flex items-center justify-center shrink-0">
                <svg viewBox="0 0 24 24" className="w-5 h-5">
                  <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                  <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                  <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                  <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
                </svg>
              </div>
              <div className="flex-1">
                <p className="text-sm font-medium text-zinc-200">Google Calendar & Gmail</p>
                <p className="text-xs text-zinc-600 mt-0.5">
                  {googleConnected
                    ? 'Connected · daily_routine profile available'
                    : 'Connect to enable the daily_routine profile'}
                </p>
              </div>
              {googleConnected ? (
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => setGoogleConnected(false)}
                >
                  Disconnect
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  loading={connecting}
                  onClick={connectGoogle}
                >
                  Connect
                </Button>
              )}
            </div>
            {googleConnected && (
              <div className="mt-3 pt-3 border-t border-zinc-800 text-xs text-zinc-600 space-y-0.5">
                <p>✓ Calendar: read access</p>
                <p>✓ Gmail: drafts only (no send without user tap)</p>
                <p>· Session-scoped tokens · revoked on End</p>
              </div>
            )}
          </Card>
        </section>

        {/* Push notifications */}
        <section className="space-y-3">
          <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-widest">Notifications</h2>
          <Card className="flex items-center justify-between py-3">
            <div>
              <p className="text-sm font-medium text-zinc-300">Approval notifications</p>
              <p className="text-xs text-zinc-600 mt-0.5">Get notified when the agent needs your approval</p>
            </div>
            <div className="w-8 h-5 rounded-full bg-indigo-600 border border-indigo-500 relative">
              <span className="absolute right-0.5 top-0.5 w-4 h-4 rounded-full bg-white shadow" />
            </div>
          </Card>
        </section>

        {/* About */}
        <section className="space-y-3">
          <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-widest">About</h2>
          <Card>
            <div className="space-y-2 text-xs font-mono">
              {[
                ['Version', 'demo-0.1.0'],
                ['Build', 'hackathon-2025'],
                ['Gateway', 'MCP + policy.yaml'],
                ['Auth', 'Bearer token (hackathon)'],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span className="text-zinc-600">{k}</span>
                  <span className="text-zinc-400">{v}</span>
                </div>
              ))}
            </div>
          </Card>
        </section>
      </div>
    </div>
  )
}
