import React, { useState } from 'react'
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity, StatusBar,
} from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import { MOCK_SESSIONS, type Decision } from '../lib/mock-data'
import { T, DecisionPill, RiskTag } from '../components/ui'

type Filter = 'all' | Decision

interface AuditEntry {
  id: string
  seq: number
  timestamp: Date
  tool: string
  decision: Decision
  reason?: string
  riskTier: 'safe' | 'sensitive' | 'critical'
  sessionId: string
  sessionTitle: string
}

function buildEntries(): AuditEntry[] {
  return MOCK_SESSIONS.flatMap(s =>
    s.toolCalls.map((t, i) => ({
      id: `${s.id}-${t.id}`,
      seq: i + 1,
      timestamp: t.timestamp,
      tool: t.tool,
      decision: t.decision as Decision,
      reason: t.reason,
      riskTier: t.riskTier,
      sessionId: s.id,
      sessionTitle: s.title,
    }))
  ).sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime())
}

function fmt(d: Date) {
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
}

interface AuditScreenProps { navigation: any }

export function AuditScreen({ navigation }: AuditScreenProps) {
  const [filter, setFilter] = useState<Filter>('all')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [exported, setExported] = useState(false)

  const all = buildEntries()
  const visible = filter === 'all' ? all : all.filter(e => e.decision === filter)

  const counts = {
    all: all.length,
    allow: all.filter(e => e.decision === 'allow').length,
    ask:   all.filter(e => e.decision === 'ask').length,
    block: all.filter(e => e.decision === 'block').length,
  }

  const chainHash = `sha256:00${all.length.toString(16).padStart(2,'0')}a3f7c2e1b896`

  function doExport() {
    setExported(true)
    setTimeout(() => setExported(false), 2200)
  }

  return (
    <SafeAreaView style={s.safe}>
      <StatusBar barStyle="light-content" />

      <FlatList
        data={visible}
        keyExtractor={item => item.id}
        contentContainerStyle={s.listContent}
        ListHeaderComponent={
          <>
            {/* Header */}
            <View style={s.header}>
              <View>
                <Text style={s.heading}>Audit log</Text>
                <Text style={s.sub}>{all.length} entries · hash-chained</Text>
              </View>
              <TouchableOpacity style={s.exportBtn} onPress={doExport} activeOpacity={0.8}>
                <Text style={s.exportIcon}>{exported ? '✓' : '↓'}</Text>
                <Text style={s.exportText}>{exported ? 'Saved' : 'Export'}</Text>
              </TouchableOpacity>
            </View>

            {/* Chain integrity */}
            <View style={s.chainCard}>
              <View style={s.chainIconWrap}>
                <Text style={s.chainIcon}>✓</Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={s.chainTitle}>Chain integrity verified</Text>
                <Text style={s.chainHash} numberOfLines={1}>{chainHash}</Text>
              </View>
              <Text style={s.chainValid}>valid</Text>
            </View>

            {/* Stats strip */}
            <View style={s.statsStrip}>
              {(['allow', 'ask', 'block'] as Decision[]).map(d => (
                <View key={d} style={s.statItem}>
                  <Text style={[s.statNum, {
                    color: d === 'allow' ? T.green : d === 'ask' ? T.amber : T.red,
                  }]}>{counts[d]}</Text>
                  <Text style={s.statLabel}>{d}</Text>
                </View>
              ))}
            </View>

            {/* Filter bar */}
            <View style={s.filterBar}>
              {(['all', 'allow', 'ask', 'block'] as const).map(f => (
                <TouchableOpacity
                  key={f}
                  style={[s.filterChip, filter === f && s.filterChipActive]}
                  onPress={() => setFilter(f)}
                  activeOpacity={0.75}
                >
                  <Text style={[s.filterText, filter === f && s.filterTextActive]}>
                    {f === 'all' ? 'All' : f}
                    {'  '}<Text style={s.filterCount}>{counts[f]}</Text>
                  </Text>
                </TouchableOpacity>
              ))}
            </View>

            {/* Feed label */}
            <Text style={s.feedLabel}>Entries</Text>
          </>
        }
        renderItem={({ item, index }) => (
          <AuditRow
            entry={item}
            isExpanded={expanded === item.id}
            onToggle={() => setExpanded(e => e === item.id ? null : item.id)}
            isFirst={index === 0}
          />
        )}
        ListEmptyComponent={
          <Text style={s.empty}>No entries match this filter</Text>
        }
      />
    </SafeAreaView>
  )
}

function AuditRow({ entry, isExpanded, onToggle, isFirst }: {
  entry: AuditEntry
  isExpanded: boolean
  onToggle: () => void
  isFirst: boolean
}) {
  const accentColor = { allow: T.green, ask: T.amber, block: T.red }[entry.decision]
  const serverIcon: Record<string, string> = {
    files: '◫', terminal: '▪', browser: '◎', desktop: '□', calendar: '▦', gmail: '◈',
  }
  const [server] = entry.tool.split('.')

  return (
    <TouchableOpacity
      style={[s.row, isFirst && s.rowFirst, isExpanded && s.rowExpanded]}
      onPress={onToggle}
      activeOpacity={0.8}
    >
      {/* Left accent line */}
      <View style={[s.rowAccent, { backgroundColor: accentColor + '70' }]} />

      <View style={s.rowBody}>
        {/* Top */}
        <View style={s.rowTop}>
          <Text style={s.rowIcon}>{serverIcon[server] ?? '◈'}</Text>
          <Text style={s.rowTool}>{entry.tool}</Text>
          <DecisionPill decision={entry.decision} />
          <Text style={s.rowTime}>{fmt(entry.timestamp)}</Text>
        </View>

        {/* Expanded detail */}
        {isExpanded && (
          <View style={s.detail}>
            <View style={s.detailRow}>
              <RiskTag tier={entry.riskTier} />
              <Text style={s.seqHash}>
                #{entry.seq}  ·  sha:{entry.seq.toString(16).padStart(4,'0')}…
              </Text>
            </View>
            {entry.reason && (
              <Text style={s.reason}>{entry.reason}</Text>
            )}
            <Text style={s.sessionRef} numberOfLines={1}>
              {entry.sessionTitle}
            </Text>
          </View>
        )}
      </View>
    </TouchableOpacity>
  )
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: T.bg },
  listContent: { paddingBottom: 50 },

  header: {
    flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between',
    paddingHorizontal: 24, paddingTop: 20, paddingBottom: 16,
  },
  heading: { color: T.text, fontSize: 26, fontWeight: '800', letterSpacing: -0.8 },
  sub: { color: T.textDim, fontSize: 12, marginTop: 2 },
  exportBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
    borderRadius: 10, paddingHorizontal: 12, paddingVertical: 8,
  },
  exportIcon: { color: T.textSub, fontSize: 13 },
  exportText: { color: T.textSub, fontSize: 12, fontWeight: '600' },

  chainCard: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    marginHorizontal: 16, marginBottom: 16,
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
    borderRadius: 16, padding: 14,
  },
  chainIconWrap: {
    width: 34, height: 34, borderRadius: 10,
    backgroundColor: T.greenDim, borderWidth: 1, borderColor: T.green + '30',
    alignItems: 'center', justifyContent: 'center',
  },
  chainIcon: { color: T.green, fontSize: 15, fontWeight: '800' },
  chainTitle: { color: T.text, fontSize: 13, fontWeight: '600' },
  chainHash: { color: T.textDim, fontSize: 10, fontFamily: T.mono, marginTop: 2 },
  chainValid: { color: T.green, fontSize: 11, fontWeight: '800' },

  statsStrip: {
    flexDirection: 'row', marginHorizontal: 16, marginBottom: 16,
    backgroundColor: T.surface, borderRadius: 16,
    borderWidth: 1, borderColor: T.border, overflow: 'hidden',
  },
  statItem: {
    flex: 1, alignItems: 'center', paddingVertical: 14,
    borderRightWidth: 1, borderRightColor: T.border,
  },
  statNum: { fontSize: 24, fontWeight: '800', fontFamily: T.mono },
  statLabel: { color: T.textDim, fontSize: 10, marginTop: 2 },

  filterBar: {
    flexDirection: 'row', gap: 6,
    paddingHorizontal: 16, marginBottom: 16,
  },
  filterChip: {
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 10,
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
  },
  filterChipActive: {
    backgroundColor: T.indigoDim, borderColor: T.indigo + '50',
  },
  filterText: { color: T.textDim, fontSize: 11, fontWeight: '600', textTransform: 'capitalize' },
  filterTextActive: { color: T.indigo },
  filterCount: { color: T.textFaint, fontWeight: '400' },

  feedLabel: {
    color: T.textDim, fontSize: 10, fontWeight: '700',
    letterSpacing: 1.8, textTransform: 'uppercase',
    paddingHorizontal: 20, marginBottom: 8,
  },

  row: {
    flexDirection: 'row', marginHorizontal: 16, marginBottom: 3,
    backgroundColor: T.surface, borderRadius: 12, overflow: 'hidden',
    borderWidth: 1, borderColor: T.border,
  },
  rowFirst: { borderColor: T.borderMid },
  rowExpanded: { borderColor: T.borderMid },
  rowAccent: { width: 3 },
  rowBody: { flex: 1, padding: 10, gap: 4 },
  rowTop: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  rowIcon: { color: T.textSub, fontSize: 11, fontFamily: T.mono },
  rowTool: { flex: 1, color: T.text, fontSize: 11, fontFamily: T.mono, fontWeight: '600' },
  rowTime: { color: T.textFaint, fontSize: 9, fontFamily: T.mono },

  detail: { gap: 5, paddingTop: 6 },
  detailRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  seqHash: { color: T.textFaint, fontSize: 10, fontFamily: T.mono },
  reason: { color: T.amber + 'cc', fontSize: 10, fontFamily: T.mono },
  sessionRef: { color: T.textDim, fontSize: 10 },

  empty: { color: T.textFaint, textAlign: 'center', paddingVertical: 40, fontSize: 13 },
})
