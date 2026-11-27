import React, { type ReactNode } from 'react'
import { StyleSheet, Text, TouchableOpacity, View, type TouchableOpacityProps, type ViewStyle } from 'react-native'
import { Feather } from '@expo/vector-icons'
import { REASONS, type Decision, type Risk, type SessionState, type ToolCall } from '../lib/mock'

// Lovable OKLCH dark palette mapped cleanly to sRGB
export const C = {
  background: '#111218',
  surface: '#0b0c11',
  foreground: '#f2f3f6',
  card: '#181a22',
  popover: '#1c1e28',
  primary: '#6b6df8',
  primaryFg: '#fafafa',
  secondary: '#242733',
  secondaryFg: '#edeef2',
  muted: '#20222d',
  mutedFg: '#9ca2b3',
  accent: '#272a3d',
  allow: '#34c78d',
  ask: '#e5a93b',
  block: '#eb555a',
  border: '#2c2f3c',
  input: '#323544',
  mono: 'monospace' as const,
}

export function BrandHeader({ onPressBrand }: { onPressBrand?: () => void }) {
  return (
    <View style={u.header}>
      <TouchableOpacity onPress={onPressBrand} activeOpacity={0.8} style={u.brandRow}>
        <View>
          <Text style={u.brandTitle}>OverWatch</Text>
          <Text style={u.brandSub}>MCP Pocket Computer</Text>
        </View>
      </TouchableOpacity>
    </View>
  )
}

const decisionMeta: Record<Decision, { label: string; icon: keyof typeof Feather.glyphMap; color: string }> = {
  allow: { label: 'ALLOW', icon: 'check-circle', color: C.allow },
  ask: { label: 'ASK', icon: 'alert-triangle', color: C.ask },
  block: { label: 'BLOCK', icon: 'slash', color: C.block },
}

export function DecisionBadge({ d }: { d: Decision }) {
  const m = decisionMeta[d]
  return (
    <View style={[u.decBadge, { backgroundColor: m.color + '18', borderColor: m.color + '4d' }]}>
      <Feather name={m.icon} size={11} color={m.color} />
      <Text style={[u.decText, { color: m.color }]}>{m.label}</Text>
    </View>
  )
}

export function RiskBadge({ r }: { r: Risk }) {
  const color = r === 'high' ? C.block : r === 'medium' ? C.ask : C.allow
  return <Text style={[u.riskText, { color }]}>● {r.toUpperCase()} RISK</Text>
}

export function StateBadge({ s, big }: { s: SessionState; big?: boolean }) {
  const map: Record<SessionState, { icon: keyof typeof Feather.glyphMap; color: string; bg: string; border: string; label: string }> = {
    ACTIVE: { icon: 'play', color: C.allow, bg: C.allow + '18', border: C.allow + '66', label: 'ACTIVE' },
    AWAITING: { icon: 'clock', color: C.ask, bg: C.ask + '18', border: C.ask + '66', label: 'NEEDS APPROVAL' },
    PAUSED: { icon: 'pause', color: C.mutedFg, bg: C.muted, border: C.border, label: 'PAUSED' },
    ENDED: { icon: 'square', color: C.mutedFg, bg: C.surface, border: C.border, label: 'ENDED' },
  }
  const m = map[s]
  return (
    <View style={[u.stateBadge, { backgroundColor: m.bg, borderColor: m.border }, big && u.stateBadgeBig]}>
      <Feather name={m.icon} size={big ? 12 : 10} color={m.color} />
      <Text style={[u.stateText, { color: m.color }, big && { fontSize: 11 }]}>{m.label}</Text>
    </View>
  )
}

export function Budget({ used, limit }: { used: number; limit: number }) {
  const pct = Math.min(100, (used / limit) * 100)
  const color = pct > 85 ? C.block : pct > 60 ? C.ask : C.primary
  return (
    <View>
      <View style={u.budgetTop}>
        <Text style={u.budgetLabel}>Budget</Text>
        <Text style={u.budgetLabel}>${used.toFixed(2)} / ${limit.toFixed(2)}</Text>
      </View>
      <View style={u.budgetTrack}>
        <View style={[u.budgetFill, { width: `${pct}%` as any, backgroundColor: color }]} />
      </View>
    </View>
  )
}

export function Card({ children, style }: { children: ReactNode; style?: ViewStyle }) {
  return <View style={[u.card, style]}>{children}</View>
}

type BtnVariant = 'primary' | 'secondary' | 'danger' | 'approve' | 'ghost'

export function Btn({
  variant = 'primary',
  children,
  style,
  disabled,
  ...props
}: TouchableOpacityProps & { variant?: BtnVariant; children: ReactNode }) {
  const v: Record<BtnVariant, { bg: string; border?: string; text: string }> = {
    primary: { bg: C.primary, text: C.primaryFg },
    secondary: { bg: C.secondary, border: C.border, text: C.secondaryFg },
    danger: { bg: C.block + '24', border: C.block + '66', text: C.block },
    approve: { bg: C.allow, text: C.background },
    ghost: { bg: 'transparent', text: C.mutedFg },
  }
  const s = v[variant]
  return (
    <TouchableOpacity
      activeOpacity={0.82}
      disabled={disabled}
      style={[
        u.btn,
        { backgroundColor: s.bg },
        s.border ? { borderWidth: 1, borderColor: s.border } : null,
        disabled ? { opacity: 0.48 } : null,
        style,
      ]}
      {...props}
    >
      {typeof children === 'string' ? (
        <Text style={[u.btnText, { color: s.text }]}>{children}</Text>
      ) : (
        children
      )}
    </TouchableOpacity>
  )
}

export const fmtTime = (iso: string) => iso.slice(11, 19) + 'Z'

export function CallRow({ c, onReview }: { c: ToolCall; onReview?: () => void }) {
  const statusLabel = {
    done: 'executed',
    pending: 'awaiting approval',
    denied: 'denied by user',
    blocked: 'blocked by policy',
    approved: 'approved · executed',
  }[c.status]

  return (
    <View style={u.callRow}>
      <View style={u.callTop}>
        <Text style={u.callSeq}>#{String(c.seq).padStart(2, '0')}</Text>
        <Text style={u.callTool} numberOfLines={1}>{c.tool}</Text>
        <DecisionBadge d={c.decision} />
      </View>

      <View style={u.callArgs}>
        {Object.entries(c.args).map(([k, val]) => (
          <View key={k} style={u.argLine}>
            <Text style={u.argKey}>{k}:</Text>
            <Text style={u.argVal}>{val}</Text>
          </View>
        ))}
      </View>

      <View style={u.callMeta}>
        <Text style={u.callReason} accessibilityHint={REASONS[c.reason]}>{c.reason}</Text>
        <Text style={u.callMetaItem}>{statusLabel}</Text>
        <Text style={u.callMetaMono}>{c.latencyMs === null ? '—' : `${c.latencyMs}ms`}</Text>
        <Text style={u.callMetaMono}>{fmtTime(c.ts)}</Text>
      </View>

      {c.status === 'pending' && onReview ? (
        <Btn
          variant="secondary"
          style={{ marginTop: 10, borderColor: C.ask + '66' }}
          onPress={onReview}
        >
          <View style={u.btnInner}>
            <Feather name="alert-triangle" size={14} color={C.ask} />
            <Text style={[u.btnText, { color: C.ask }]}>Review request</Text>
          </View>
        </Btn>
      ) : null}
    </View>
  )
}

const u = StyleSheet.create({
  header: {
    height: 54,
    borderBottomWidth: 1,
    borderBottomColor: C.border,
    backgroundColor: C.background,
    paddingHorizontal: 16,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  brandRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  brandTitle: { color: C.foreground, fontSize: 15, fontWeight: '700', letterSpacing: -0.2 },
  brandSub: { color: C.mutedFg, fontSize: 10, fontFamily: C.mono },

  decBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    borderRadius: 6,
    borderWidth: 1,
    paddingHorizontal: 6,
    paddingVertical: 3,
  },
  decText: { fontSize: 10, fontFamily: C.mono, fontWeight: '700' },

  riskText: { fontSize: 10, fontFamily: C.mono, fontWeight: '600' },

  stateBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 3,
    alignSelf: 'flex-start',
  },
  stateBadgeBig: { paddingHorizontal: 11, paddingVertical: 5 },
  stateText: { fontSize: 10, fontFamily: C.mono, fontWeight: '700', letterSpacing: 0.6 },

  budgetTop: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 5 },
  budgetLabel: { fontSize: 11, fontFamily: C.mono, color: C.mutedFg },
  budgetTrack: { height: 6, borderRadius: 999, backgroundColor: C.muted, overflow: 'hidden' },
  budgetFill: { height: '100%', borderRadius: 999 },

  card: {
    backgroundColor: C.card,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: C.border,
    padding: 14,
  },

  btn: {
    minHeight: 44,
    borderRadius: 10,
    paddingHorizontal: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  btnInner: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  btnText: { fontSize: 14, fontWeight: '700' },

  callRow: {
    borderRadius: 10,
    borderWidth: 1,
    borderColor: C.border,
    backgroundColor: C.surface,
    padding: 12,
  },
  callTop: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  callSeq: { color: C.mutedFg, fontSize: 11, fontFamily: C.mono },
  callTool: { flex: 1, color: C.foreground, fontSize: 13, fontFamily: C.mono, fontWeight: '700' },
  callArgs: { marginTop: 8, gap: 3 },
  argLine: { flexDirection: 'row', gap: 6 },
  argKey: { color: C.mutedFg, fontSize: 12, fontFamily: C.mono },
  argVal: { flex: 1, color: C.foreground, fontSize: 12, fontFamily: C.mono },
  callMeta: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 8, alignItems: 'center' },
  callReason: { color: C.foreground + 'cc', fontSize: 11, fontFamily: C.mono },
  callMetaItem: { color: C.mutedFg, fontSize: 11 },
  callMetaMono: { color: C.mutedFg, fontSize: 11, fontFamily: C.mono },
})
