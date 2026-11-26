import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { MOCK_SESSIONS, type Session } from '../lib/mock-data'
import { StatusDot, TierBadge, Card, Button } from '../components/ui'
import { formatTime } from '../lib/utils'
import { cn } from '../lib/utils'

const STATE_LABEL: Record<Session['state'], string> = {
  CREATED: 'Created', PLANNING: 'Planning', STARTING: 'Starting',
  ACTIVE: 'Active', PAUSED: 'Paused', ENDING: 'Ending',
  ENDED: 'Ended', FAILED: 'Failed',
}

export function HomePage() {
  const navigate = useNavigate()
  const [sessions] = useState<Session[]>(MOCK_SESSIONS)

  const active = sessions.filter(s => s.state === 'ACTIVE' || s.state === 'PAUSED' || s.state === 'STARTING')
  const past = sessions.filter(s => s.state === 'ENDED' || s.state === 'FAILED')

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-8 space-y-8">

        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-zinc-100">Sessions</h1>
            <p className="text-sm text-zinc-500 mt-0.5">
              {active.length > 0
                ? `${active.length} session${active.length > 1 ? 's' : ''} running`
                : 'No active sessions'}
            </p>
          </div>
          <Button onClick={() => navigate('/new')} size="md">
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
            </svg>
            New task
          </Button>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: 'Total sessions', value: sessions.length.toString() },
            { label: 'Tool calls today', value: sessions.reduce((a, s) => a + s.toolCalls.length, 0).toString() },
            { label: 'Blocked', value: sessions.reduce((a, s) => a + s.toolCalls.filter(t => t.decision === 'block').length, 0).toString() },
          ].map(stat => (
            <Card key={stat.label} className="text-center py-5">
              <p className="text-2xl font-bold text-zinc-100 font-mono">{stat.value}</p>
              <p className="text-xs text-zinc-600 mt-1">{stat.label}</p>
            </Card>
          ))}
        </div>

        {/* Active sessions */}
        {active.length > 0 && (
          <section>
            <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-widest mb-3">Active</h2>
            <div className="space-y-2">
              {active.map(s => <SessionRow key={s.id} session={s} />)}
            </div>
          </section>
        )}

        {/* Past sessions */}
        {past.length > 0 && (
          <section>
            <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-widest mb-3">Recent</h2>
            <div className="space-y-2">
              {past.map(s => <SessionRow key={s.id} session={s} />)}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}

function SessionRow({ session: s }: { session: Session }) {
  const navigate = useNavigate()
  const lastCall = s.toolCalls[s.toolCalls.length - 1]
  const blockedCount = s.toolCalls.filter(t => t.decision === 'block').length

  return (
    <Card onClick={() => navigate(`/session/${s.id}`)} className="group">
      <div className="flex items-start gap-3">
        {/* State dot */}
        <div className="mt-1 shrink-0">
          <StatusDot state={s.state} />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <p className="text-sm font-medium text-zinc-200 truncate">{s.title}</p>
          </div>
          <div className="flex items-center gap-3 text-xs text-zinc-600">
            <span className="font-mono">{STATE_LABEL[s.state]}</span>
            <span>·</span>
            <span className="capitalize">{s.profile}</span>
            <span>·</span>
            <span>{s.toolCalls.length} calls</span>
            {blockedCount > 0 && (
              <>
                <span>·</span>
                <span className="text-rose-500">{blockedCount} blocked</span>
              </>
            )}
          </div>
          {lastCall && (
            <p className="text-xs text-zinc-700 font-mono mt-1 truncate">
              Last: {lastCall.tool} · {formatTime(lastCall.timestamp)}
            </p>
          )}
        </div>

        {/* Right side */}
        <div className="flex flex-col items-end gap-1.5 shrink-0">
          <TierBadge tier={s.bandwidthTier} />
          {s.pendingApprovals.length > 0 && (
            <span className="text-[10px] font-medium text-amber-400 bg-amber-400/10 border border-amber-400/20 rounded px-1.5 py-0.5 animate-pulse">
              {s.pendingApprovals.length} pending
            </span>
          )}
        </div>
      </div>

      {/* Budget strip */}
      <div className="mt-3 h-0.5 bg-zinc-800 rounded-full overflow-hidden">
        <div
          className={cn(
            'h-full rounded-full transition-all',
            s.budget.used / s.budget.total > 0.8 ? 'bg-rose-500' :
            s.budget.used / s.budget.total > 0.6 ? 'bg-amber-500' : 'bg-indigo-500/60',
          )}
          style={{ width: `${(s.budget.used / s.budget.total) * 100}%` }}
        />
      </div>
    </Card>
  )
}
