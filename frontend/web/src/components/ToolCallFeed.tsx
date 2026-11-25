import { useState, useEffect, useRef } from 'react'
import { formatTime } from '../lib/utils'
import type { ToolCallEvent } from '../lib/mock-data'
import { DecisionBadge, RiskBadge } from './ui'
import { cn } from '../lib/utils'

interface ToolCallFeedProps {
  events: ToolCallEvent[]
  className?: string
  maxItems?: number
}

function ToolIcon({ tool }: { tool: string }) {
  const [server] = tool.split('.')
  const icons: Record<string, string> = {
    files: '📁',
    terminal: '⬛',
    browser: '◎',
    desktop: '□',
    calendar: '▦',
    gmail: '✉',
  }
  return <span className="text-sm leading-none">{icons[server] ?? '◈'}</span>
}

export function ToolCallFeed({ events, className, maxItems = 50 }: ToolCallFeedProps) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const [prevLen, setPrevLen] = useState(events.length)

  useEffect(() => {
    if (events.length !== prevLen) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
      setPrevLen(events.length)
    }
  }, [events.length, prevLen])

  const visible = events.slice(-maxItems)

  return (
    <div className={cn('flex flex-col gap-0.5 overflow-y-auto', className)}>
      {visible.length === 0 && (
        <div className="flex items-center justify-center py-8">
          <p className="text-xs text-zinc-600">Waiting for agent activity…</p>
        </div>
      )}
      {visible.map((ev, i) => (
        <ToolCallRow key={ev.id} event={ev} isNew={i === visible.length - 1} />
      ))}
      <div ref={bottomRef} />
    </div>
  )
}

function ToolCallRow({ event, isNew }: { event: ToolCallEvent; isNew: boolean }) {
  const [expanded, setExpanded] = useState(false)

  const borderColor = {
    allow: 'border-l-emerald-500/50',
    ask: 'border-l-amber-500/50',
    block: 'border-l-rose-500/50',
  }[event.decision]

  const argStr = Object.entries(event.args)
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(', ')
    .slice(0, 80)

  return (
    <div
      className={cn(
        'group px-3 py-2 rounded-lg border-l-2 bg-zinc-900/60 hover:bg-zinc-900 transition-all cursor-pointer',
        borderColor,
        isNew && 'animate-slide-in-up',
      )}
      onClick={() => setExpanded(e => !e)}
    >
      <div className="flex items-center gap-2 min-w-0">
        <ToolIcon tool={event.tool} />
        <span className="text-xs font-mono text-zinc-200 truncate flex-1">{event.tool}</span>
        <DecisionBadge decision={event.decision} />
        <span className="text-[10px] font-mono text-zinc-600 shrink-0">
          {formatTime(event.timestamp)}
        </span>
        {event.durationMs && (
          <span className="text-[10px] font-mono text-zinc-600 shrink-0">
            {event.durationMs > 1000
              ? `${(event.durationMs / 1000).toFixed(1)}s`
              : `${event.durationMs}ms`}
          </span>
        )}
      </div>

      {/* Args preview */}
      <div className="mt-0.5 ml-6">
        <p className="text-[11px] font-mono text-zinc-600 truncate">{argStr}</p>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="mt-2 ml-6 space-y-1.5 animate-fade-in">
          <div className="rounded-md bg-zinc-950 border border-zinc-800 p-2">
            <pre className="text-[11px] font-mono text-zinc-400 whitespace-pre-wrap break-all">
              {JSON.stringify(event.args, null, 2)}
            </pre>
          </div>
          <div className="flex items-center gap-2">
            <RiskBadge tier={event.riskTier} />
            <span className="text-xs text-zinc-600">
              {event.reversible ? 'Reversible' : 'Irreversible'}
            </span>
            {event.reason && (
              <span className="text-xs text-zinc-500 font-mono">{event.reason}</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
