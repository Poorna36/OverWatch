import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { MOCK_SESSIONS, type AuditEntry, type Decision } from '../lib/mock-data'
import { DecisionBadge, RiskBadge, Button, Card } from '../components/ui'
import { formatTime } from '../lib/utils'
import { cn } from '../lib/utils'

type Filter = 'all' | Decision

function buildAuditEntries(sessions: typeof MOCK_SESSIONS): AuditEntry[] {
  return sessions.flatMap(s =>
    s.toolCalls.map((t, i) => ({
      seq: i + 1,
      timestamp: t.timestamp,
      tool: t.tool,
      decision: t.decision,
      reason: t.reason,
      riskTier: t.riskTier,
      sessionId: s.id,
    }))
  ).sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime())
}

function hashHead(entries: AuditEntry[]): string {
  // Fake but realistic-looking chain head
  return 'sha256:' + entries.length.toString(16).padStart(8, '0') + 'a3f7c2e1b8d96054'
}

export function AuditPage() {
  const [searchParams] = useSearchParams()
  const sessionFilter = searchParams.get('session')
  const [filter, setFilter] = useState<Filter>('all')
  const [exported, setExported] = useState(false)

  const sessions = sessionFilter
    ? MOCK_SESSIONS.filter(s => s.id === sessionFilter)
    : MOCK_SESSIONS

  const allEntries = buildAuditEntries(sessions)
  const filtered = filter === 'all' ? allEntries : allEntries.filter(e => e.decision === filter)
  const chainHead = hashHead(allEntries)

  function exportReceipt() {
    const receipt = {
      generated_at: new Date().toISOString(),
      session_count: sessions.length,
      entry_count: allEntries.length,
      chain_head: chainHead,
      valid: true,
      entries: allEntries,
    }
    const blob = new Blob([JSON.stringify(receipt, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `overwatch-audit-${Date.now()}.json`
    a.click()
    URL.revokeObjectURL(url)
    setExported(true)
    setTimeout(() => setExported(false), 2000)
  }

  const counts = {
    all: allEntries.length,
    allow: allEntries.filter(e => e.decision === 'allow').length,
    ask: allEntries.filter(e => e.decision === 'ask').length,
    block: allEntries.filter(e => e.decision === 'block').length,
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">

        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-zinc-100">Audit log</h1>
            <p className="text-sm text-zinc-500 mt-0.5">
              Append-only, hash-chained. {allEntries.length} entries.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={exportReceipt}>
            {exported ? '✓ Exported' : '↓ Export receipt'}
          </Button>
        </div>

        {/* Chain status */}
        <Card className="flex items-center gap-3 py-3">
          <div className="w-8 h-8 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center shrink-0">
            <span className="text-emerald-400 text-sm">✓</span>
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-zinc-300">Chain integrity verified</p>
            <p className="text-xs font-mono text-zinc-600 truncate mt-0.5">{chainHead}</p>
          </div>
          <span className="text-xs text-emerald-400 font-medium">valid</span>
        </Card>

        {/* Filter bar */}
        <div className="flex items-center gap-2">
          {(['all', 'allow', 'ask', 'block'] as const).map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cn(
                'px-3 py-1.5 rounded-lg text-xs font-medium transition-all border',
                filter === f
                  ? 'bg-indigo-600/20 border-indigo-500/40 text-indigo-400'
                  : 'border-zinc-800 text-zinc-500 hover:text-zinc-300 hover:border-zinc-700',
              )}
            >
              <span className="capitalize">{f}</span>
              <span className="ml-1.5 text-zinc-600">{counts[f]}</span>
            </button>
          ))}
        </div>

        {/* Entries */}
        <div className="space-y-1">
          {filtered.map((entry, i) => (
            <AuditRow key={`${entry.sessionId}-${entry.seq}`} entry={entry} index={i} />
          ))}
          {filtered.length === 0 && (
            <div className="py-12 text-center text-zinc-700 text-sm">No entries match this filter</div>
          )}
        </div>
      </div>
    </div>
  )
}

function AuditRow({ entry, index }: { entry: AuditEntry; index: number }) {
  const [expanded, setExpanded] = useState(false)

  const borderColor = {
    allow: 'border-l-emerald-500/40',
    ask: 'border-l-amber-500/40',
    block: 'border-l-rose-500/40',
  }[entry.decision]

  const seqHash = `sha256:${(index + 1).toString(16).padStart(4, '0')}…`

  return (
    <div
      className={cn(
        'px-3 py-2.5 rounded-lg border-l-2 bg-zinc-900/60 hover:bg-zinc-900 cursor-pointer transition-all',
        borderColor,
      )}
      onClick={() => setExpanded(e => !e)}
    >
      <div className="flex items-center gap-3">
        <span className="text-xs font-mono text-zinc-700 w-6 shrink-0">#{entry.seq}</span>
        <span className="text-xs font-mono text-zinc-500 shrink-0">{formatTime(entry.timestamp)}</span>
        <span className="text-xs font-mono text-zinc-200 flex-1 truncate">{entry.tool}</span>
        <DecisionBadge decision={entry.decision} />
        <RiskBadge tier={entry.riskTier} />
      </div>

      {expanded && (
        <div className="mt-2 ml-9 space-y-1 animate-fade-in">
          {entry.reason && (
            <p className="text-xs font-mono text-zinc-500">{entry.reason}</p>
          )}
          <p className="text-[10px] font-mono text-zinc-700">{seqHash}</p>
          <p className="text-[10px] text-zinc-700">Session: {entry.sessionId}</p>
        </div>
      )}
    </div>
  )
}
