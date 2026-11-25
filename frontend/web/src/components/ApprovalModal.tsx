import { useState, useEffect } from 'react'
import type { ApprovalRequest } from '../lib/mock-data'
import { RiskBadge } from './ui'
import { cn } from '../lib/utils'

interface ApprovalModalProps {
  approval: ApprovalRequest
  onApprove: (id: string) => void
  onDeny: (id: string) => void
}

function formatArg(val: unknown): string {
  if (typeof val === 'string') return val.length > 40 ? val.slice(0, 40) + '…' : val
  return JSON.stringify(val)
}

export function ApprovalModal({ approval, onApprove, onDeny }: ApprovalModalProps) {
  const [secondsLeft, setSecondsLeft] = useState(
    Math.max(0, Math.floor((approval.expiresAt.getTime() - Date.now()) / 1000))
  )

  useEffect(() => {
    const id = setInterval(() => {
      setSecondsLeft(s => {
        if (s <= 1) { onDeny(approval.id); return 0 }
        return s - 1
      })
    }, 1000)
    return () => clearInterval(id)
  }, [approval.id, approval.expiresAt, onDeny])

  const totalSecs = Math.floor((approval.expiresAt.getTime() - approval.createdAt.getTime()) / 1000)
  const pct = (secondsLeft / totalSecs) * 100
  const circumference = 2 * Math.PI * 20
  const dashOffset = circumference - (pct / 100) * circumference

  const isUrgent = secondsLeft < 30

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm animate-fade-in" />

      {/* Modal */}
      <div className={cn(
        'relative z-10 w-full max-w-sm rounded-2xl border bg-zinc-900 p-5 shadow-2xl animate-slide-in-up',
        isUrgent ? 'border-amber-500/40' : 'border-zinc-700',
      )}>
        {/* Header */}
        <div className="flex items-start justify-between mb-4">
          <div>
            <p className="text-xs text-zinc-500 font-medium uppercase tracking-widest mb-1">Approval Required</p>
            <h3 className="text-lg font-semibold text-zinc-100">{approval.tool}</h3>
          </div>

          {/* Countdown ring */}
          <div className="relative w-12 h-12 shrink-0">
            <svg className="w-12 h-12 -rotate-90" viewBox="0 0 48 48">
              <circle cx="24" cy="24" r="20" fill="none" stroke="#27272a" strokeWidth="4" />
              <circle
                cx="24" cy="24" r="20" fill="none"
                stroke={isUrgent ? '#fbbf24' : '#818cf8'}
                strokeWidth="4"
                strokeLinecap="round"
                strokeDasharray={circumference}
                strokeDashoffset={dashOffset}
                style={{ transition: 'stroke-dashoffset 1s linear' }}
              />
            </svg>
            <span className={cn(
              'absolute inset-0 flex items-center justify-center text-xs font-mono font-bold',
              isUrgent ? 'text-amber-400' : 'text-indigo-400',
            )}>
              {secondsLeft}
            </span>
          </div>
        </div>

        {/* Args table */}
        <div className="rounded-lg bg-zinc-950 border border-zinc-800 p-3 mb-4 space-y-1.5">
          {Object.entries(approval.args).map(([k, v]) => (
            <div key={k} className="flex items-start gap-2">
              <span className="text-xs font-mono text-zinc-600 shrink-0 pt-0.5">{k}</span>
              <span className="text-xs font-mono text-zinc-300 break-all">{formatArg(v)}</span>
            </div>
          ))}
        </div>

        {/* Risk info */}
        <div className="flex items-center gap-2 mb-5">
          <RiskBadge tier={approval.riskTier} />
          {!approval.reversible && (
            <span className="text-xs text-amber-400 flex items-center gap-1">
              <span>⚠</span>
              <span>Cannot be undone</span>
            </span>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-2">
          <button
            onClick={() => onDeny(approval.id)}
            className="flex-1 py-2.5 rounded-xl border border-zinc-700 text-sm font-medium text-zinc-400 hover:text-zinc-200 hover:border-zinc-600 hover:bg-zinc-800 transition-all"
          >
            Deny
          </button>
          <button
            onClick={() => onApprove(approval.id)}
            className="flex-1 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-sm font-semibold text-white transition-all"
          >
            Approve
          </button>
        </div>
      </div>
    </div>
  )
}
