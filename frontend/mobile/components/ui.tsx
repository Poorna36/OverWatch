import React from 'react'
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native'
import type { Decision, RiskTier, BandwidthTier, SessionState } from '../lib/mock-data'

// ─── Colors ──────────────────────────────────────────────────────────────────
export const C = {
  bg: '#09090b',
  surface: '#111113',
  surface2: '#18181b',
  border: '#27272a',
  borderSubtle: '#1c1c1f',
  text: '#fafafa',
  textMuted: '#71717a',
  textSubtle: '#3f3f46',
  accent: '#818cf8',
  accentDim: '#312e81',
  green: '#34d399',
  amber: '#fbbf24',
  red: '#f87171',
  indigo: '#6366f1',
  violet: '#8b5cf6',
}

// ─── Status dot ──────────────────────────────────────────────────────────────
interface StatusDotProps { state: SessionState; size?: number }

export function StatusDot({ state, size = 8 }: StatusDotProps) {
  const color: Record<SessionState, string> = {
    ACTIVE: C.green, PAUSED: C.amber, ENDED: C.textMuted,
    FAILED: C.red, STARTING: C.accent, PLANNING: C.violet,
    CREATED: C.textSubtle, ENDING: C.textSubtle,
  }
  return <View style={[styles.dot, { width: size, height: size, borderRadius: size / 2, backgroundColor: color[state] }]} />
}

// ─── Decision badge ───────────────────────────────────────────────────────────
interface DecisionBadgeProps { decision: Decision }

export function DecisionBadge({ decision }: DecisionBadgeProps) {
  const cfg = {
    allow: { color: C.green, bg: '#052e1611', border: '#34d39930', icon: '✓', label: 'Allowed' },
    ask:   { color: C.amber, bg: '#451a0311', border: '#fbbf2430', icon: '⚠', label: 'Ask' },
    block: { color: C.red,   bg: '#450a0a11', border: '#f8717130', icon: '✕', label: 'Blocked' },
  }[decision]

  return (
    <View style={[styles.badge, { backgroundColor: cfg.bg, borderColor: cfg.border }]}>
      <Text style={[styles.badgeText, { color: cfg.color }]}>{cfg.icon} {cfg.label}</Text>
    </View>
  )
}

// ─── Risk badge ───────────────────────────────────────────────────────────────
interface RiskBadgeProps { tier: RiskTier }

export function RiskBadge({ tier }: RiskBadgeProps) {
  const cfg = {
    safe:      { color: C.textMuted, bg: '#27272a44', border: C.border },
    sensitive: { color: C.amber,     bg: '#451a0311', border: '#fbbf2430' },
    critical:  { color: C.red,       bg: '#450a0a11', border: '#f8717130' },
  }[tier]

  return (
    <View style={[styles.badge, { backgroundColor: cfg.bg, borderColor: cfg.border }]}>
      <Text style={[styles.badgeText, { color: cfg.color }]}>{tier}</Text>
    </View>
  )
}

// ─── Tier badge ───────────────────────────────────────────────────────────────
interface TierBadgeProps { tier: BandwidthTier }

export function TierBadge({ tier }: TierBadgeProps) {
  const cfg = {
    video:       { color: C.accent,   bg: '#312e8122', border: '#818cf830', icon: '▶' },
    screenshots: { color: C.violet,   bg: '#2e1065aa', border: '#8b5cf630', icon: '◉' },
    text:        { color: C.textMuted, bg: '#27272a44', border: C.border,   icon: '≡' },
  }[tier]

  return (
    <View style={[styles.badge, { backgroundColor: cfg.bg, borderColor: cfg.border }]}>
      <Text style={[styles.badgeText, { color: cfg.color }]}>{cfg.icon} {tier}</Text>
    </View>
  )
}

// ─── Budget bar ───────────────────────────────────────────────────────────────
interface BudgetBarProps { used: number; total: number; costUsd: number }

export function BudgetBar({ used, total, costUsd }: BudgetBarProps) {
  const pct = Math.min((used / total) * 100, 100)
  const fillColor = pct > 80 ? C.red : pct > 60 ? C.amber : C.indigo

  return (
    <View style={styles.budgetWrap}>
      <View style={styles.budgetHeader}>
        <Text style={styles.budgetLabel}>Budget</Text>
        <Text style={styles.budgetValue}>{used}/{total} · ${costUsd.toFixed(3)}</Text>
      </View>
      <View style={styles.budgetTrack}>
        <View style={[styles.budgetFill, { width: `${pct}%` as any, backgroundColor: fillColor }]} />
      </View>
    </View>
  )
}

// ─── Button ───────────────────────────────────────────────────────────────────
interface ButtonProps {
  label: string
  onPress: () => void
  variant?: 'primary' | 'outline' | 'danger' | 'success'
  size?: 'sm' | 'md'
  disabled?: boolean
}

export function Btn({ label, onPress, variant = 'primary', size = 'md', disabled }: ButtonProps) {
  const cfg = {
    primary: { bg: C.indigo,  border: C.indigo,       text: '#fff' },
    outline: { bg: 'transparent', border: C.border,   text: C.textMuted },
    danger:  { bg: '#450a0a44', border: '#f8717144',  text: C.red },
    success: { bg: '#05261644', border: '#34d39944',  text: C.green },
  }[variant]

  const pad = size === 'sm' ? { paddingVertical: 6, paddingHorizontal: 12 } : { paddingVertical: 10, paddingHorizontal: 18 }
  const fontSize = size === 'sm' ? 12 : 14

  return (
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled}
      activeOpacity={0.75}
      style={[styles.btn, pad, { backgroundColor: cfg.bg, borderColor: cfg.border, opacity: disabled ? 0.4 : 1 }]}
    >
      <Text style={[styles.btnText, { color: cfg.text, fontSize }]}>{label}</Text>
    </TouchableOpacity>
  )
}

// ─── Styles ───────────────────────────────────────────────────────────────────
const styles = StyleSheet.create({
  dot: {},
  badge: {
    borderWidth: 1,
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
    alignSelf: 'flex-start',
  },
  badgeText: {
    fontSize: 10,
    fontFamily: 'monospace',
    fontWeight: '600',
  },
  budgetWrap: { gap: 4 },
  budgetHeader: { flexDirection: 'row', justifyContent: 'space-between' },
  budgetLabel: { fontSize: 10, color: C.textMuted, fontFamily: 'monospace' },
  budgetValue: { fontSize: 10, color: C.textMuted, fontFamily: 'monospace' },
  budgetTrack: { height: 3, backgroundColor: C.surface2, borderRadius: 2, overflow: 'hidden' },
  budgetFill: { height: 3, borderRadius: 2 },
  btn: { borderWidth: 1, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  btnText: { fontWeight: '600', letterSpacing: 0.1 },
})
