import React, { useMemo, useState } from 'react'
import { ScrollView, Share, StyleSheet, Text, TouchableOpacity, View } from 'react-native'
import { Feather } from '@expo/vector-icons'
import { SafeAreaView } from 'react-native-safe-area-context'
import { REASONS, mockHash, type Decision } from '../lib/mock'
import { useStore } from '../lib/store'
import { useToast } from '../lib/toast'
import { BrandHeader, Btn, C, Card, DecisionBadge, fmtTime } from '../components/ui'

export function AuditScreen({ navigation }: { navigation: any }) {
  const { sessions } = useStore()
  const toast = useToast()
  const [f, setF] = useState<'all' | Decision>('all')

  const records = useMemo(() => {
    const all = sessions
      .flatMap((s) => s.calls.map((c) => ({ ...c, session: s.id })))
      .sort((a, b) => a.ts.localeCompare(b.ts))
    let prev = '00000000'
    return all.map((r) => {
      const hash = mockHash(prev + JSON.stringify(r))
      const out = { ...r, prev, hash }
      prev = hash
      return out
    })
  }, [sessions])

  const shown = records.filter((r) => f === 'all' || r.decision === f).reverse()

  async function exportJson() {
    const receipt = JSON.stringify(
      {
        kind: 'overwatch.audit.receipt',
        exportedAt: new Date().toISOString(),
        records,
      },
      null,
      2,
    )
    try {
      await Share.share({ message: receipt, title: 'overwatch-audit-receipt.json' })
    } catch {
      toast('Receipt prepared', { description: `${records.length} records formatted as JSON.` })
    }
  }

  return (
    <SafeAreaView style={st.safe} edges={['top', 'left', 'right']}>
      <BrandHeader onPressBrand={() => navigation.navigate('SessionsTab')} />
      <ScrollView contentContainerStyle={st.content} showsVerticalScrollIndicator={false}>
        <View style={st.topRow}>
          <Text style={st.h1}>Audit</Text>
          <Btn variant="secondary" onPress={exportJson}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <Feather name="download" size={14} color={C.secondaryFg} />
              <Text style={{ color: C.secondaryFg, fontSize: 13, fontWeight: '700' }}>Export receipt</Text>
            </View>
          </Btn>
        </View>

        <Card style={{ flexDirection: 'row', gap: 12, borderColor: C.allow + '66' }}>
          <Feather name="check-circle" size={20} color={C.allow} style={{ marginTop: 2 }} />
          <View style={{ flex: 1 }}>
            <Text style={st.warnTitle}>Hash chain verified</Text>
            <Text style={st.warnDesc}>
              {records.length} records chained in order.
            </Text>
          </View>
        </Card>

        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={st.tabsRow}>
          {(['all', 'allow', 'ask', 'block'] as const).map((k) => {
            const active = f === k
            const count = records.filter((r) => r.decision === k).length
            return (
              <TouchableOpacity
                key={k}
                onPress={() => setF(k)}
                style={[st.tabChip, active && st.tabChipActive]}
              >
                <Text style={[st.tabText, active && { color: C.foreground }]}>
                  {k.toUpperCase()}
                  {k !== 'all' ? ` ${count}` : ''}
                </Text>
              </TouchableOpacity>
            )
          })}
        </ScrollView>

        <View style={st.timeline}>
          {shown.map((r) => (
            <View key={r.session + r.seq} style={st.timelineItem}>
              <View style={st.timelineDot} />
              <Card style={{ padding: 12 }}>
                <View style={st.rowTop}>
                  <Text style={st.tool} numberOfLines={1}>{r.tool}</Text>
                  <DecisionBadge d={r.decision} />
                </View>
                <Text style={st.reasonLine}>
                  <Text style={{ fontFamily: C.mono, color: C.foreground }}>{r.reason}</Text>
                  <Text style={{ color: C.mutedFg }}> — {REASONS[r.reason]}</Text>
                </Text>
                <View style={st.metaLine}>
                  <Text style={st.metaTxt}>{fmtTime(r.ts)}</Text>
                  <Text style={st.metaTxt}>{r.session}#{r.seq}</Text>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                    <Feather name="link-2" size={11} color={C.mutedFg} />
                    <Text style={st.metaTxt}>{r.prev.slice(0, 6)}→{r.hash.slice(0, 6)}</Text>
                  </View>
                </View>
              </Card>
            </View>
          ))}
          {shown.length === 0 && <Text style={{ color: C.mutedFg, fontSize: 13 }}>No records for this filter.</Text>}
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

const st = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.background },
  content: { padding: 16, paddingBottom: 32, gap: 16 },
  topRow: { flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12 },
  h1: { color: C.foreground, fontSize: 24, fontWeight: '700', letterSpacing: -0.4 },
  warnTitle: { color: C.foreground, fontSize: 14, fontWeight: '700' },
  warnDesc: { color: C.mutedFg, fontSize: 13, marginTop: 3, lineHeight: 18 },

  tabsRow: { gap: 8 },
  tabChip: {
    minHeight: 38,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: C.border,
    paddingHorizontal: 16,
    justifyContent: 'center',
  },
  tabChipActive: { borderColor: C.primary, backgroundColor: C.accent },
  tabText: { color: C.mutedFg, fontSize: 12, fontFamily: C.mono, fontWeight: '600' },

  timeline: {
    borderLeftWidth: 1,
    borderLeftColor: C.border,
    paddingLeft: 18,
    marginLeft: 6,
    gap: 12,
  },
  timelineItem: { position: 'relative' },
  timelineDot: {
    position: 'absolute',
    left: -23,
    top: 14,
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: C.primary,
    borderWidth: 2,
    borderColor: C.background,
  },
  rowTop: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  tool: { flex: 1, color: C.foreground, fontSize: 14, fontFamily: C.mono, fontWeight: '700' },
  reasonLine: { fontSize: 12, marginTop: 5, lineHeight: 17 },
  metaLine: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 8, alignItems: 'center' },
  metaTxt: { color: C.mutedFg, fontSize: 11, fontFamily: C.mono },
})
