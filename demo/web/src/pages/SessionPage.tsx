import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { MOCK_SESSIONS, DEMO_EVENTS, type Session, type ToolCallEvent, type ApprovalRequest } from '../lib/mock-data'
import { LiveView } from '../components/LiveView'
import { ToolCallFeed } from '../components/ToolCallFeed'
import { ApprovalModal } from '../components/ApprovalModal'
import { StatusDot, TierBadge, BudgetBar, Button } from '../components/ui'
import { formatDuration } from '../lib/utils'

export function SessionPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const seed = MOCK_SESSIONS.find(s => s.id === id) ?? MOCK_SESSIONS[0]
  const [session, setSession] = useState<Session>({ ...seed, toolCalls: [...seed.toolCalls] })
  const [demoRunning, setDemoRunning] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [showLiveView, setShowLiveView] = useState(true)

  // Live clock
  useEffect(() => {
    const start = Date.now()
    const id = setInterval(() => setElapsed(Date.now() - start), 1000)
    return () => clearInterval(id)
  }, [])

  // Demo auto-play
  async function runDemo() {
    setDemoRunning(true)
    setSession(s => ({ ...s, toolCalls: [], state: 'STARTING', pendingApprovals: [] }))
    await new Promise(r => setTimeout(r, 1000))
    setSession(s => ({ ...s, state: 'ACTIVE' }))

    let seq = 1
    for (const { delay, event } of DEMO_EVENTS) {
      await new Promise(r => setTimeout(r, delay))
      const newCall: ToolCallEvent = {
        ...event,
        id: `demo_${seq}`,
        seq,
        timestamp: new Date(),
      }
      setSession(s => ({
        ...s,
        toolCalls: [...s.toolCalls, newCall],
        budget: { ...s.budget, used: Math.min(s.budget.used + 6, s.budget.total) },
        pendingApprovals: event.decision === 'ask'
          ? [...s.pendingApprovals, {
              id: `apr_demo_${seq}`,
              tool: event.tool,
              args: event.args,
              riskTier: event.riskTier,
              reversible: event.reversible,
              createdAt: new Date(),
              expiresAt: new Date(Date.now() + 120_000),
            } as ApprovalRequest]
          : s.pendingApprovals,
      }))
      seq++
    }
    setDemoRunning(false)
  }

  const handlePause = () => setSession(s => ({
    ...s, state: s.state === 'ACTIVE' ? 'PAUSED' : 'ACTIVE',
  }))

  const handleEnd = () => {
    setSession(s => ({ ...s, state: 'ENDED', endedAt: new Date() }))
    setTimeout(() => navigate('/'), 2000)
  }

  const handleApprove = useCallback((aid: string) => {
    setSession(s => ({
      ...s,
      pendingApprovals: s.pendingApprovals.filter(a => a.id !== aid),
      toolCalls: s.toolCalls.map(t =>
        t.decision === 'ask' && t.id.includes('5') ? { ...t, decision: 'allow' } : t,
      ),
    }))
  }, [])

  const handleDeny = useCallback((aid: string) => {
    setSession(s => ({
      ...s,
      pendingApprovals: s.pendingApprovals.filter(a => a.id !== aid),
    }))
  }, [])

  const isActive = session.state === 'ACTIVE'
  const isPaused = session.state === 'PAUSED'
  const isEnded = session.state === 'ENDED'

  return (
    <div className="flex-1 flex flex-col overflow-hidden relative">
      {/* Top bar */}
      <div className="flex items-center gap-4 px-6 py-3 border-b border-zinc-800/60 bg-zinc-950/80 glass shrink-0">
        {/* Back */}
        <button onClick={() => navigate('/')} className="text-zinc-600 hover:text-zinc-400 transition-colors">
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6"/>
          </svg>
        </button>

        {/* Status */}
        <div className="flex items-center gap-2">
          <StatusDot state={session.state} />
          <span className="text-sm font-semibold text-zinc-200">{session.state}</span>
        </div>

        <TierBadge tier={session.bandwidthTier} />

        <span className="text-xs font-mono text-zinc-600 capitalize">{session.profile}</span>

        {session.startedAt && (
          <span className="text-xs font-mono text-zinc-700">
            {formatDuration(elapsed)}
          </span>
        )}

        <div className="flex-1" />

        {/* Demo play button */}
        {!isEnded && (
          <Button
            variant="outline"
            size="sm"
            onClick={runDemo}
            loading={demoRunning}
            disabled={demoRunning}
          >
            {demoRunning ? 'Demo running…' : '▶ Run demo'}
          </Button>
        )}

        {/* Controls */}
        {!isEnded && (
          <>
            <Button variant="outline" size="sm" onClick={handlePause}>
              {isPaused ? 'Resume' : 'Pause'}
            </Button>
            <Button variant="danger" size="sm" onClick={handleEnd}>
              End
            </Button>
          </>
        )}

        {isEnded && (
          <span className="text-xs text-zinc-500">Session ended · redirecting…</span>
        )}
      </div>

      {/* Title strip */}
      <div className="px-6 py-3 border-b border-zinc-800/40 shrink-0">
        <p className="text-sm text-zinc-400 truncate">{session.title}</p>
      </div>

      {/* Main content */}
      <div className="flex-1 overflow-hidden flex gap-0">

        {/* Left: live view + budget */}
        <div className="w-[420px] shrink-0 flex flex-col border-r border-zinc-800/40 p-4 gap-4 overflow-y-auto">
          {/* Live view toggle */}
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-zinc-500 uppercase tracking-widest">Live view</span>
            <button
              onClick={() => setShowLiveView(v => !v)}
              className="text-xs text-zinc-700 hover:text-zinc-500 transition-colors"
            >
              {showLiveView ? 'collapse' : 'expand'}
            </button>
          </div>

          {showLiveView && (
            <LiveView
              sessionId={session.id}
              active={isActive}
              tier={session.bandwidthTier}
              className="h-56"
            />
          )}

          <BudgetBar
            used={session.budget.used}
            total={session.budget.total}
            costUsd={session.budget.costUsd}
            maxCostUsd={session.budget.maxCostUsd}
          />

          {/* Quick stats */}
          <div className="grid grid-cols-3 gap-2">
            {[
              { label: 'Total', value: session.toolCalls.length, color: 'text-zinc-300' },
              { label: 'Allowed', value: session.toolCalls.filter(t => t.decision === 'allow').length, color: 'text-emerald-400' },
              { label: 'Blocked', value: session.toolCalls.filter(t => t.decision === 'block').length, color: 'text-rose-400' },
            ].map(stat => (
              <div key={stat.label} className="rounded-lg bg-zinc-900 border border-zinc-800 p-2.5 text-center">
                <p className={`text-lg font-bold font-mono ${stat.color}`}>{stat.value}</p>
                <p className="text-[10px] text-zinc-600 mt-0.5">{stat.label}</p>
              </div>
            ))}
          </div>

          {/* Approval pending */}
          {session.pendingApprovals.length > 0 && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2.5">
              <div className="flex items-center gap-2 text-sm text-amber-400 font-medium">
                <span className="animate-pulse">⚠</span>
                <span>{session.pendingApprovals.length} approval pending</span>
              </div>
              <p className="text-xs text-amber-500/60 mt-0.5">
                Tap to review: {session.pendingApprovals[0]?.tool}
              </p>
            </div>
          )}

          {/* Paused banner */}
          {isPaused && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-center">
              <p className="text-sm text-amber-400 font-medium">Session paused</p>
              <p className="text-xs text-amber-500/50 mt-0.5">Agent calls are being held</p>
            </div>
          )}

          {/* Ended */}
          {isEnded && (
            <div className="rounded-xl border border-zinc-700 bg-zinc-900 px-3 py-3 text-center space-y-1">
              <p className="text-sm text-zinc-400 font-medium">Workspace destroyed</p>
              <p className="text-xs text-zinc-600">Container removed · credentials revoked</p>
              <button
                onClick={() => navigate(`/audit?session=${session.id}`)}
                className="text-xs text-indigo-400 hover:underline mt-1"
              >
                View audit receipt →
              </button>
            </div>
          )}
        </div>

        {/* Right: tool call feed */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800/40 shrink-0">
            <span className="text-xs font-medium text-zinc-500 uppercase tracking-widest">Tool-call feed</span>
            <span className="text-xs font-mono text-zinc-700">{session.toolCalls.length} events</span>
          </div>
          <ToolCallFeed
            events={session.toolCalls}
            className="flex-1 overflow-y-auto px-4 py-3"
          />
        </div>
      </div>

      {/* Approval modal */}
      {session.pendingApprovals.length > 0 && (
        <ApprovalModal
          approval={session.pendingApprovals[0]}
          onApprove={handleApprove}
          onDeny={handleDeny}
        />
      )}
    </div>
  )
}
