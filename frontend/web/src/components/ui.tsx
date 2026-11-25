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
    ACTIVE:   'bg-[#a6e3a1]', // catppuccin green
    PAUSED:   'bg-[#f9e2af]', // catppuccin yellow
    ENDED:    'bg-[#6c7086]', // overlay0
    FAILED:   'bg-[#f38ba8]', // catppuccin red
    STARTING: 'bg-[#cba6f7]', // mauve
    PLANNING: 'bg-[#b4befe]', // lavender
    CREATED:  'bg-[#a6adc8]', // subtext0
    ENDING:   'bg-[#a6adc8]', // subtext0
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
    allow: 'text-[#a6e3a1] bg-[#a6e3a1]/10 border-[#a6e3a1]/20',
    ask:   'text-[#f9e2af] bg-[#f9e2af]/10 border-[#f9e2af]/20',
    block: 'text-[#f38ba8] bg-[#f38ba8]/10 border-[#f38ba8]/20',
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
    safe:      'text-[#a6adc8] bg-[#a6adc8]/10 border-[#a6adc8]/20',
    sensitive: 'text-[#f9e2af] bg-[#f9e2af]/10 border-[#f9e2af]/20',
    critical:  'text-[#f38ba8] bg-[#f38ba8]/10 border-[#f38ba8]/20',
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
    video:       'text-[#cba6f7] bg-[#cba6f7]/10 border-[#cba6f7]/20',
    screenshots: 'text-[#b4befe] bg-[#b4befe]/10 border-[#b4befe]/20',
    text:        'text-[#a6adc8] bg-[#a6adc8]/10 border-[#a6adc8]/20',
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
  const color = pct > 80 ? 'bg-[#f38ba8]' : pct > 60 ? 'bg-[#f9e2af]' : 'bg-[#cba6f7]'

  return (
    <div className={cn('space-y-1', className)}>
      <div className="flex items-center justify-between">
        <span className="text-xs text-[#6c7086] font-mono">Budget</span>
        <div className="flex items-center gap-2 text-xs text-[#a6adc8] font-mono">
          <span>{used}/{total} calls</span>
          <span className="text-[#45475a]">·</span>
          <span>${costUsd.toFixed(3)}</span>
        </div>
      </div>
      <div className="h-1 bg-[#313244] rounded-full overflow-hidden">
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
      <div className="w-12 h-12 rounded-2xl bg-[#1e1e2e] border border-[#45475a] flex items-center justify-center text-[#6c7086]">
        {icon}
      </div>
      <div>
        <p className="text-sm font-medium text-[#cdd6f4]">{title}</p>
        <p className="text-xs text-[#6c7086] mt-1 max-w-xs">{description}</p>
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
        'rounded-xl bg-[#1e1e2e] border border-[#45475a] p-4',
        glow && 'glow-accent',
        onClick && 'cursor-pointer hover:border-[#585b70] hover:bg-[#313244]/80 transition-all',
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
  const base = 'inline-flex items-center justify-center gap-2 font-medium rounded-lg transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#cba6f7]/40 disabled:opacity-40 disabled:cursor-not-allowed'

  const variants = {
    default: 'bg-[#cba6f7] hover:bg-[#b48ef5] text-[#11111b]',
    outline: 'border border-[#45475a] hover:border-[#585b70] hover:bg-[#313244] text-[#a6adc8]',
    ghost:   'hover:bg-[#313244] text-[#6c7086] hover:text-[#cdd6f4]',
    danger:  'bg-[#f38ba8]/15 hover:bg-[#f38ba8]/25 border border-[#f38ba8]/30 text-[#f38ba8]',
    success: 'bg-[#a6e3a1]/15 hover:bg-[#a6e3a1]/25 border border-[#a6e3a1]/30 text-[#a6e3a1]',
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
