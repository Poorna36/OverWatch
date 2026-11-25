import React from 'react'
import { cn } from '../lib/utils'
import type { Decision, RiskTier } from '../lib/mock-data'

// ─── Status dot ──────────────────────────────────────────────────────────────

interface StatusDotProps {
  state: 'ACTIVE' | 'PAUSED' | 'ENDED' | 'FAILED' | 'STARTING' | 'PLANNING' | 'CREATED' | 'ENDING'
  className?: string
}

export function StatusDot({ state, className }: StatusDotProps) {
  const color = {
    ACTIVE: 'bg-emerald-400',
    PAUSED: 'bg-amber-400',
    ENDED: 'bg-zinc-500',
    FAILED: 'bg-rose-400',
    STARTING: 'bg-indigo-400',
    PLANNING: 'bg-violet-400',
    CREATED: 'bg-zinc-400',
    ENDING: 'bg-zinc-400',
  }[state]

  const pulse = state === 'ACTIVE' || state === 'STARTING'

  return (
    <span className={cn('relative inline-flex h-2 w-2', className)}>
      {pulse && (
        <span className={cn('absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping', color)} />
      )}
      <span className={cn('relative inline-flex rounded-full h-2 w-2', color)} />
    </span>
  )
}

// ─── Decision badge ──────────────────────────────────────────────────────────

interface DecisionBadgeProps {
  decision: Decision
  className?: string
}

export function DecisionBadge({ decision, className }: DecisionBadgeProps) {
  const styles = {
    allow: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20',
    ask: 'text-amber-400 bg-amber-400/10 border-amber-400/20',
    block: 'text-rose-400 bg-rose-400/10 border-rose-400/20',
  }[decision]

  const icon = { allow: '✓', ask: '⚠', block: '✕' }[decision]
  const label = { allow: 'Allowed', ask: 'Ask', block: 'Blocked' }[decision]

  return (
    <span className={cn(
      'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-mono border',
      styles, className,
    )}>
      <span>{icon}</span>
      <span>{label}</span>
    </span>
  )
}

// ─── Risk badge ──────────────────────────────────────────────────────────────

interface RiskBadgeProps {
  tier: RiskTier
  className?: string
}

export function RiskBadge({ tier, className }: RiskBadgeProps) {
  const styles = {
    safe: 'text-zinc-400 bg-zinc-400/10 border-zinc-400/20',
    sensitive: 'text-amber-400 bg-amber-400/10 border-amber-400/20',
    critical: 'text-rose-400 bg-rose-400/10 border-rose-400/20',
  }[tier]

  return (
    <span className={cn('inline-flex px-1.5 py-0.5 rounded text-xs border capitalize', styles, className)}>
      {tier}
    </span>
  )
}

// ─── Tier badge ───────────────────────────────────────────────────────────────

interface TierBadgeProps {
  tier: 'video' | 'screenshots' | 'text'
  className?: string
}

export function TierBadge({ tier, className }: TierBadgeProps) {
  const styles = {
    video: 'text-indigo-400 bg-indigo-400/10 border-indigo-400/20',
    screenshots: 'text-violet-400 bg-violet-400/10 border-violet-400/20',
    text: 'text-zinc-400 bg-zinc-400/10 border-zinc-400/20',
  }[tier]

  const icons = { video: '▶', screenshots: '◉', text: '≡' }

  return (
    <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border', styles, className)}>
      <span>{icons[tier]}</span>
      <span className="font-medium">{tier}</span>
    </span>
  )
}

// ─── Budget bar ───────────────────────────────────────────────────────────────

interface BudgetBarProps {
  used: number
  total: number
  costUsd: number
  maxCostUsd?: number
  className?: string
}

export function BudgetBar({ used, total, costUsd, maxCostUsd: _maxCostUsd, className }: BudgetBarProps) {
  const pct = Math.min((used / total) * 100, 100)
  const color = pct > 80 ? 'bg-rose-500' : pct > 60 ? 'bg-amber-500' : 'bg-indigo-500'

  return (
    <div className={cn('space-y-1', className)}>
      <div className="flex items-center justify-between">
        <span className="text-xs text-zinc-500 font-mono">Budget</span>
        <div className="flex items-center gap-2 text-xs text-zinc-400 font-mono">
          <span>{used}/{total} calls</span>
          <span className="text-zinc-600">·</span>
          <span>${costUsd.toFixed(3)}</span>
        </div>
      </div>
      <div className="h-1 bg-zinc-800 rounded-full overflow-hidden">
        <div
          className={cn('h-full rounded-full transition-all duration-500', color)}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

// ─── Spinner ─────────────────────────────────────────────────────────────────

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={cn('animate-spin', className)}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z" />
    </svg>
  )
}

// ─── Empty state ─────────────────────────────────────────────────────────────

interface EmptyStateProps {
  icon: React.ReactNode
  title: string
  description: string
  action?: React.ReactNode
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center gap-3">
      <div className="w-12 h-12 rounded-2xl bg-zinc-900 border border-zinc-800 flex items-center justify-center text-zinc-600">
        {icon}
      </div>
      <div>
        <p className="text-sm font-medium text-zinc-300">{title}</p>
        <p className="text-xs text-zinc-600 mt-1 max-w-xs">{description}</p>
      </div>
      {action}
    </div>
  )
}

// ─── Card ────────────────────────────────────────────────────────────────────

interface CardProps {
  children: React.ReactNode
  className?: string
  onClick?: () => void
  glow?: boolean
}

export function Card({ children, className, onClick, glow }: CardProps) {
  return (
    <div
      onClick={onClick}
      className={cn(
        'rounded-xl bg-zinc-900 border border-zinc-800 p-4',
        glow && 'glow-accent',
        onClick && 'cursor-pointer hover:border-zinc-700 hover:bg-zinc-800/80 transition-all',
        className,
      )}
    >
      {children}
    </div>
  )
}

// ─── Button ──────────────────────────────────────────────────────────────────

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'outline' | 'ghost' | 'danger' | 'success'
  size?: 'sm' | 'md' | 'lg'
  loading?: boolean
}

export function Button({
  variant = 'default', size = 'md', loading, children, className, disabled, ...rest
}: ButtonProps) {
  const base = 'inline-flex items-center justify-center gap-2 font-medium rounded-lg transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/50 disabled:opacity-40 disabled:cursor-not-allowed'

  const variants = {
    default: 'bg-indigo-600 hover:bg-indigo-500 text-white',
    outline: 'border border-zinc-700 hover:border-zinc-600 hover:bg-zinc-800 text-zinc-300',
    ghost: 'hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200',
    danger: 'bg-rose-600/20 hover:bg-rose-600/30 border border-rose-500/30 text-rose-400 hover:text-rose-300',
    success: 'bg-emerald-600/20 hover:bg-emerald-600/30 border border-emerald-500/30 text-emerald-400 hover:text-emerald-300',
  }

  const sizes = {
    sm: 'px-2.5 py-1.5 text-xs',
    md: 'px-3.5 py-2 text-sm',
    lg: 'px-5 py-2.5 text-base',
  }

  return (
    <button
      className={cn(base, variants[variant], sizes[size], className)}
      disabled={disabled || loading}
      {...rest}
    >
      {loading && <Spinner className="w-3.5 h-3.5" />}
      {children}
    </button>
  )
}
