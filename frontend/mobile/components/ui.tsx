import React, { useEffect, useRef } from 'react'
import {
  View, Text, StyleSheet, TouchableOpacity, Animated,
  ViewStyle, TextStyle,
} from 'react-native'
import type { Decision, RiskTier, BandwidthTier, SessionState } from '../lib/mock-data'

// ─── Design tokens — Catppuccin Mocha ─────────────────────────────────────────
export const T = {
  // Backgrounds
  bg:        '#11111b', // crust
  surface:   '#1e1e2e', // base
  elevated:  '#181825', // mantle
  overlay:   '#313244', // surface0

  // Borders
  border:    '#45475a', // surface1
  borderMid: '#585b70', // surface2

  // Text
  text:      '#cdd6f4', // text
  textSub:   '#a6adc8', // subtext0
  textDim:   '#6c7086', // overlay0
  textFaint: '#45475a', // surface1

  // Accent — mauve as primary
  indigo:    '#cba6f7', // mauve
  indigoDim: '#2a1f3d', // mauve dim
  violet:    '#b4befe', // lavender
  violetDim: '#1e1f38', // lavender dim

  // Semantic
  green:     '#a6e3a1', // green
  greenDim:  '#1a2b1a', // green dim
  amber:     '#f9e2af', // yellow
  amberDim:  '#2b2210', // yellow dim
  red:       '#f38ba8', // red
  redDim:    '#2b1420', // red dim

  // Extras (Catppuccin specific)
  blue:      '#89b4fa', // blue
  teal:      '#94e2d5', // teal
  peach:     '#fab387', // peach
  pink:      '#f5c2e7', // pink
  sky:       '#89dceb', // sky

  // Mono
  mono:      'monospace' as const,
}

// ─── Animated pulse dot (for ACTIVE state) ────────────────────────────────────
export function PulseDot({ color = T.green, size = 8 }: { color?: string; size?: number }) {
  const scale = useRef(new Animated.Value(1)).current
  const opacity = useRef(new Animated.Value(0.6)).current

  useEffect(() => {
    const anim = Animated.loop(
      Animated.sequence([
        Animated.parallel([
          Animated.timing(scale, { toValue: 1.8, duration: 900, useNativeDriver: true }),
          Animated.timing(opacity, { toValue: 0, duration: 900, useNativeDriver: true }),
        ]),
        Animated.parallel([
          Animated.timing(scale, { toValue: 1, duration: 0, useNativeDriver: true }),
          Animated.timing(opacity, { toValue: 0.6, duration: 0, useNativeDriver: true }),
        ]),
      ])
    )
    anim.start()
    return () => anim.stop()
  }, [])

  return (
    <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
      <Animated.View style={{
        position: 'absolute', width: size, height: size,
        borderRadius: size / 2, backgroundColor: color,
        transform: [{ scale }], opacity,
      }} />
      <View style={{ width: size * 0.65, height: size * 0.65, borderRadius: size, backgroundColor: color }} />
    </View>
  )
}

// ─── Status indicator ─────────────────────────────────────────────────────────
export function StateChip({ state }: { state: SessionState }) {
  const cfg: Record<SessionState, { color: string; bg: string; label: string; pulse: boolean }> = {
    ACTIVE:   { color: T.green,  bg: T.greenDim,  label: 'Active',   pulse: true },
    PAUSED:   { color: T.amber,  bg: T.amberDim,  label: 'Paused',   pulse: false },
    STARTING: { color: T.indigo, bg: T.indigoDim, label: 'Starting', pulse: true },
    PLANNING: { color: T.violet, bg: T.violetDim, label: 'Planning', pulse: true },
    ENDED:    { color: T.textDim,bg: T.surface,   label: 'Ended',    pulse: false },
    FAILED:   { color: T.red,    bg: T.redDim,    label: 'Failed',   pulse: false },
    CREATED:  { color: T.textDim,bg: T.surface,   label: 'Created',  pulse: false },
    ENDING:   { color: T.textDim,bg: T.surface,   label: 'Ending',   pulse: false },
  }
  const { color, bg, label, pulse } = cfg[state]
  return (
    <View style={[u.chip, { backgroundColor: bg, borderColor: color + '44' }]}>
      {pulse ? <PulseDot color={color} size={6} /> : <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: color }} />}
      <Text style={[u.chipText, { color }]}>{label}</Text>
    </View>
  )
}

// ─── Decision pill ────────────────────────────────────────────────────────────
export function DecisionPill({ decision }: { decision: Decision }) {
  const cfg = {
    allow: { color: T.green, bg: T.greenDim, border: T.green + '30', symbol: '✓', label: 'allowed' },
    ask:   { color: T.amber, bg: T.amberDim, border: T.amber + '30', symbol: '?', label: 'ask' },
    block: { color: T.red,   bg: T.redDim,   border: T.red   + '30', symbol: '✕', label: 'blocked' },
  }[decision]
  return (
    <View style={[u.pill, { backgroundColor: cfg.bg, borderColor: cfg.border }]}>
      <Text style={[u.pillText, { color: cfg.color }]}>{cfg.symbol} {cfg.label}</Text>
    </View>
  )
}

// ─── Risk tag ─────────────────────────────────────────────────────────────────
export function RiskTag({ tier }: { tier: RiskTier }) {
  const cfg = {
    safe:      { color: T.textDim, label: 'safe' },
    sensitive: { color: T.amber,   label: 'sensitive' },
    critical:  { color: T.red,     label: 'critical' },
  }[tier]
  return <Text style={[u.riskTag, { color: cfg.color }]}>{cfg.label}</Text>
}

// ─── Tier indicator ───────────────────────────────────────────────────────────
export function TierDot({ tier }: { tier: BandwidthTier }) {
  const color = { video: T.indigo, screenshots: T.violet, text: T.textDim }[tier]
  const label = { video: 'video', screenshots: 'screen', text: 'text' }[tier]
  return (
    <View style={u.tierWrap}>
      <View style={[u.tierDot, { backgroundColor: color }]} />
      <Text style={[u.tierLabel, { color }]}>{label}</Text>
    </View>
  )
}

// ─── Budget strip ─────────────────────────────────────────────────────────────
export function BudgetStrip({ used, total, costUsd }: { used: number; total: number; costUsd: number }) {
  const pct = Math.min((used / total) * 100, 100)
  const color = pct > 80 ? T.red : pct > 55 ? T.amber : T.indigo
  return (
    <View style={u.budgetRow}>
      <View style={u.budgetTrack}>
        <View style={[u.budgetFill, { width: `${pct}%` as any, backgroundColor: color }]} />
      </View>
      <Text style={u.budgetLabel}>{used}/{total} · ${costUsd.toFixed(3)}</Text>
    </View>
  )
}

// ─── Ghost button ─────────────────────────────────────────────────────────────
interface GhostBtnProps {
  label: string
  onPress: () => void
  color?: string
  style?: ViewStyle
}
export function GhostBtn({ label, onPress, color = T.textSub, style }: GhostBtnProps) {
  return (
    <TouchableOpacity onPress={onPress} activeOpacity={0.7} style={[u.ghostBtn, style]}>
      <Text style={[u.ghostBtnText, { color }]}>{label}</Text>
    </TouchableOpacity>
  )
}

// ─── Primary button ───────────────────────────────────────────────────────────
export function PrimaryBtn({ label, onPress, loading, disabled }: {
  label: string; onPress: () => void; loading?: boolean; disabled?: boolean
}) {
  return (
    <TouchableOpacity
      onPress={onPress} activeOpacity={0.85}
      disabled={disabled || loading}
      style={[u.primaryBtn, (disabled || loading) && { opacity: 0.4 }]}
    >
      <Text style={u.primaryBtnText}>{loading ? '…' : label}</Text>
    </TouchableOpacity>
  )
}

// ─── Shared styles ────────────────────────────────────────────────────────────
export const u = StyleSheet.create({
  chip: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    paddingHorizontal: 10, paddingVertical: 5,
    borderRadius: 999, borderWidth: 1,
  },
  chipText: { fontSize: 11, fontWeight: '700', letterSpacing: 0.3 },

  pill: {
    paddingHorizontal: 7, paddingVertical: 3,
    borderRadius: 6, borderWidth: 1, alignSelf: 'flex-start',
  },
  pillText: { fontSize: 10, fontWeight: '700', fontFamily: T.mono },

  riskTag: { fontSize: 10, fontFamily: T.mono, opacity: 0.8 },

  tierWrap: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  tierDot: { width: 5, height: 5, borderRadius: 3 },
  tierLabel: { fontSize: 10, fontWeight: '600', letterSpacing: 0.5 },

  budgetRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  budgetTrack: { flex: 1, height: 2, backgroundColor: T.border, borderRadius: 1 },
  budgetFill: { height: 2, borderRadius: 1 },
  budgetLabel: { fontSize: 10, color: T.textDim, fontFamily: T.mono },

  ghostBtn: {
    paddingHorizontal: 14, paddingVertical: 8,
    borderRadius: 10, borderWidth: 1, borderColor: T.border,
    backgroundColor: T.surface,
  },
  ghostBtnText: { fontSize: 13, fontWeight: '600' },

  primaryBtn: {
    backgroundColor: T.indigo,
    borderRadius: 14, paddingVertical: 15,
    alignItems: 'center',
  },
  primaryBtnText: { color: '#fff', fontSize: 15, fontWeight: '700', letterSpacing: 0.2 },
})
