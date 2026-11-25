import React, { useState } from 'react'
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  SafeAreaView, StatusBar,
} from 'react-native'
import { MOCK_SESSIONS, type AuditEntry, type Decision } from '../lib/mock-data'
import { DecisionBadge, RiskBadge, C } from '../components/ui'

function buildEntries() {
  return MOCK_SESSIONS.flatMap((s, si) =>
    s.toolCalls.map((t, i) => ({
      seq: i + 1,
      timestamp: t.timestamp,
      tool: t.tool,
      decision: t.decision,
      reason: t.reason,
      riskTier: t.riskTier,
      sessionId: s.id,
      sessionTitle: s.title,
    }))
  ).sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime())
}

function formatTime(d: Date) {
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
}

interface AuditScreenProps { navigation: any }

export function AuditScreen({ navigation }: AuditScreenProps) {
  const [filter, setFilter] = useState<'all' | Decision>('all')
  const [exported, setExported] = useState(false)

  const all = buildEntries()
  const visible = filter === 'all' ? all : all.filter(e => e.decision === filter)

  const counts = {
    all: all.length,
    allow: all.filter(e => e.decision === 'allow').length,
    ask: all.filter(e => e.decision === 'ask').length,
    block: all.filter(e => e.decision === 'block').length,
  }

  return (
    <SafeAreaView style={s.safe}>
      <StatusBar barStyle="light-content" backgroundColor={C.bg} />

      {/* Header */}
      <View style={s.header}>
        <View>
          <Text style={s.heading}>Audit log</Text>
          <Text style={s.sub}>{all.length} entries · hash-chained</Text>
        </View>
        <TouchableOpacity
          style={s.exportBtn}
          onPress={() => { setExported(true); setTimeout(() => setExported(false), 2000) }}
          activeOpacity={0.8}
        >
          <Text style={s.exportBtnText}>{exported ? '✓ Done' : '↓ Export'}</Text>
        </TouchableOpacity>
      </View>

      {/* Chain status */}
      <View style={s.chainCard}>
        <View style={s.chainIcon}><Text style={s.chainIconText}>✓</Text></View>
        <View style={{ flex: 1 }}>
          <Text style={s.chainTitle}>Chain integrity verified</Text>
          <Text style={s.chainHash} numberOfLines={1}>
            sha256:00{all.length.toString(16).padStart(2, '0')}a3f7c2e1b8d96054
          </Text>
        </View>
        <Text style={s.chainValid}>valid</Text>
      </View>

      {/* Filters */}
      <View style={s.filterRow}>
        {(['all', 'allow', 'ask', 'block'] as const).map(f => (
          <TouchableOpacity
            key={f}
            style={[s.filterBtn, filter === f && s.filterBtnActive]}
            onPress={() => setFilter(f)}
            activeOpacity={0.8}
          >
            <Text style={[s.filterText, filter === f && s.filterTextActive]}>
              {f} {counts[f]}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      <ScrollView style={s.scroll} contentContainerStyle={s.content}>
        {visible.map((entry, i) => <AuditRow key={`${entry.sessionId}-${entry.seq}-${i}`} entry={entry} />)}
        {visible.length === 0 && <Text style={s.empty}>No entries match</Text>}
      </ScrollView>
    </SafeAreaView>
  )
}

function AuditRow({ entry }: { entry: ReturnType<typeof buildEntries>[0] }) {
  const [expanded, setExpanded] = useState(false)
  const borderColor = { allow: '#34d39940', ask: '#fbbf2440', block: '#f8717140' }[entry.decision]

  return (
    <TouchableOpacity
      style={[s.row, { borderLeftColor: borderColor }]}
      onPress={() => setExpanded(e => !e)}
      activeOpacity={0.8}
    >
      <View style={s.rowTop}>
        <Text style={s.rowSeq}>#{entry.seq}</Text>
        <Text style={s.rowTime}>{formatTime(entry.timestamp)}</Text>
        <Text style={s.rowTool}>{entry.tool}</Text>
        <DecisionBadge decision={entry.decision} />
      </View>
      {expanded && (
        <View style={s.rowExpanded}>
          <RiskBadge tier={entry.riskTier} />
          {entry.reason && <Text style={s.rowReason}>{entry.reason}</Text>}
          <Text style={s.rowSession} numberOfLines={1}>{entry.sessionTitle}</Text>
        </View>
      )}
    </TouchableOpacity>
  )
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.bg },

  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 20, paddingTop: 20, paddingBottom: 12,
  },
  heading: { color: C.text, fontSize: 24, fontWeight: '700', letterSpacing: -0.5 },
  sub: { color: C.textMuted, fontSize: 12, marginTop: 2 },
  exportBtn: {
    backgroundColor: C.surface, borderWidth: 1, borderColor: C.border,
    paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10,
  },
  exportBtnText: { color: C.textMuted, fontSize: 12, fontWeight: '600' },

  chainCard: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    marginHorizontal: 16, marginBottom: 12,
    backgroundColor: C.surface, borderWidth: 1, borderColor: C.border,
    borderRadius: 14, padding: 12,
  },
  chainIcon: {
    width: 32, height: 32, borderRadius: 8,
    backgroundColor: '#05261622', borderWidth: 1, borderColor: '#34d39944',
    alignItems: 'center', justifyContent: 'center',
  },
  chainIconText: { color: C.green, fontSize: 14 },
  chainTitle: { color: C.text, fontSize: 13, fontWeight: '500' },
  chainHash: { color: C.textMuted, fontSize: 10, fontFamily: 'monospace', marginTop: 1 },
  chainValid: { color: C.green, fontSize: 12, fontWeight: '600' },

  filterRow: {
    flexDirection: 'row', gap: 8, paddingHorizontal: 16, marginBottom: 12,
  },
  filterBtn: {
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8,
    borderWidth: 1, borderColor: C.border, backgroundColor: C.surface,
  },
  filterBtnActive: { borderColor: '#818cf860', backgroundColor: '#312e8120' },
  filterText: { color: C.textMuted, fontSize: 11, fontWeight: '600', textTransform: 'capitalize' },
  filterTextActive: { color: C.accent },

  scroll: { flex: 1 },
  content: { paddingHorizontal: 16, paddingBottom: 40, gap: 4 },

  row: {
    backgroundColor: C.surface, borderRadius: 10, padding: 12,
    borderLeftWidth: 2, gap: 6,
  },
  rowTop: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  rowSeq: { color: C.textSubtle, fontSize: 10, fontFamily: 'monospace', width: 24 },
  rowTime: { color: C.textMuted, fontSize: 10, fontFamily: 'monospace' },
  rowTool: { color: C.text, fontSize: 11, fontFamily: 'monospace', flex: 1 },
  rowExpanded: { gap: 4, paddingTop: 4 },
  rowReason: { color: C.textMuted, fontSize: 10, fontFamily: 'monospace' },
  rowSession: { color: C.textSubtle, fontSize: 10 },

  empty: { color: C.textSubtle, textAlign: 'center', paddingVertical: 40 },
})
