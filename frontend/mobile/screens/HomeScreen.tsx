import React from 'react'
import { ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native'
import { Feather } from '@expo/vector-icons'
import { SafeAreaView } from 'react-native-safe-area-context'
import { PROFILES, type Session } from '../lib/mock'
import { useStore } from '../lib/store'
import { BrandHeader, Btn, Budget, C, Card, DecisionBadge, StateBadge, fmtTime } from '../components/ui'

export function HomeScreen({ navigation }: { navigation: any }) {
  const { sessions, reset } = useStore()
  const live = sessions.filter((s) => s.state !== 'ENDED')
  const recent = sessions.filter((s) => s.state === 'ENDED')
  const feed = sessions
    .flatMap((s) => s.calls.map((c) => ({ ...c, sid: s.id })))
    .sort((a, b) => b.ts.localeCompare(a.ts))
    .slice(0, 5)
  const pending = sessions.reduce((n, s) => n + s.calls.filter((c) => c.status === 'pending').length, 0)

  return (
    <SafeAreaView style={st.safe} edges={['top', 'left', 'right']}>
      <BrandHeader onPressBrand={() => navigation.navigate('SessionsTab')} />
      <ScrollView contentContainerStyle={st.content} showsVerticalScrollIndicator={false}>
        <View style={st.topRow}>
          <View style={{ flex: 1 }}>
            <Text style={st.h1}>Sessions</Text>
            <Text style={st.sub}>
              {live.length} live · {pending} awaiting your approval
            </Text>
          </View>
          <Btn onPress={() => navigation.navigate('NewTaskTab')}>
            <View style={st.btnRow}>
              <Feather name="plus" size={16} color={C.primaryFg} />
              <Text style={st.btnPrimaryText}>New task</Text>
            </View>
          </Btn>
        </View>

        {sessions.length === 0 ? (
          <Card style={st.emptyCard}>
            <Feather name="shield" size={40} color={C.primary} style={{ alignSelf: 'center' }} />
            <Text style={st.emptyTitle}>No sessions yet</Text>
            <Text style={st.emptyDesc}>
              Start a task and the agent runs in its own hardened container. Every tool call passes the policy gateway first.
            </Text>
            <View style={st.emptyActions}>
              <Btn onPress={() => navigation.navigate('NewTaskTab')}>
                <View style={st.btnRow}>
                  <Feather name="plus" size={15} color={C.primaryFg} />
                  <Text style={st.btnPrimaryText}>Start a task</Text>
                </View>
              </Btn>
              <Btn variant="secondary" onPress={reset}>
                <View style={st.btnRow}>
                  <Feather name="rotate-ccw" size={15} color={C.secondaryFg} />
                  <Text style={st.btnSecText}>Restore sessions</Text>
                </View>
              </Btn>
            </View>
          </Card>
        ) : (
          <>
            <View style={st.section}>
              <Text style={st.sectionLabel}>ACTIVE</Text>
              {live.map((s) => (
                <SessionCard key={s.id} session={s} onPress={() => navigation.navigate('Session', { id: s.id })} />
              ))}
              {live.length === 0 && <Text style={st.mutedText}>No live sessions.</Text>}
            </View>

            {recent.length > 0 && (
              <View style={st.section}>
                <Text style={st.sectionLabel}>RECENT</Text>
                {recent.map((s) => (
                  <SessionCard key={s.id} session={s} onPress={() => navigation.navigate('Session', { id: s.id })} />
                ))}
              </View>
            )}

            <View style={st.section}>
              <View style={st.activityHeader}>
                <Feather name="activity" size={13} color={C.mutedFg} />
                <Text style={st.sectionLabel}>RECENT ACTIVITY</Text>
              </View>
              <Card style={{ padding: 0, overflow: 'hidden' }}>
                {feed.map((c, idx) => (
                  <View key={c.sid + c.seq} style={[st.feedRow, idx > 0 && st.feedBorder]}>
                    <Text style={st.feedTool} numberOfLines={1}>
                      {c.tool} <Text style={{ color: C.mutedFg }}>· {c.reason}</Text>
                    </Text>
                    <Text style={st.feedTime}>{fmtTime(c.ts)}</Text>
                    <DecisionBadge d={c.decision} />
                  </View>
                ))}
              </Card>
            </View>
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  )
}

function SessionCard({ session: s, onPress }: { session: Session; onPress: () => void }) {
  const p = PROFILES[s.profile]
  const last = s.calls[s.calls.length - 1]
  return (
    <TouchableOpacity activeOpacity={0.85} onPress={onPress}>
      <Card>
        <View style={st.cardTop}>
          <View style={{ flex: 1 }}>
            <StateBadge s={s.state} />
            <Text style={st.cardTitle} numberOfLines={1}>{s.title}</Text>
            <Text style={st.cardMeta}>
              {p.name} profile · {s.calls.length} tool calls · {s.id}
            </Text>
          </View>
          <Feather name="chevron-right" size={20} color={C.mutedFg} style={{ marginTop: 4 }} />
        </View>
        {last ? (
          <Text style={st.lastCall} numberOfLines={1}>
            last: {last.tool} → {last.decision.toUpperCase()}
          </Text>
        ) : null}
        <View style={{ marginTop: 12 }}>
          <Budget used={s.budget.used} limit={s.budget.limit} />
        </View>
      </Card>
    </TouchableOpacity>
  )
}

const st = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.background },
  content: { padding: 16, paddingBottom: 32, gap: 20 },
  topRow: { flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12 },
  h1: { color: C.foreground, fontSize: 24, fontWeight: '700', letterSpacing: -0.4 },
  sub: { color: C.mutedFg, fontSize: 13, marginTop: 2 },
  btnRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  btnPrimaryText: { color: C.primaryFg, fontSize: 13, fontWeight: '700' },
  btnSecText: { color: C.secondaryFg, fontSize: 13, fontWeight: '700' },

  emptyCard: { paddingVertical: 32, alignItems: 'center', gap: 10 },
  emptyTitle: { color: C.foreground, fontSize: 16, fontWeight: '700', marginTop: 6 },
  emptyDesc: { color: C.mutedFg, fontSize: 13, textAlign: 'center', maxWidth: 280, lineHeight: 19 },
  emptyActions: { flexDirection: 'row', gap: 10, marginTop: 10 },

  section: { gap: 10 },
  sectionLabel: { color: C.mutedFg, fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  mutedText: { color: C.mutedFg, fontSize: 13 },

  cardTop: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 },
  cardTitle: { color: C.foreground, fontSize: 15, fontWeight: '700', marginTop: 8 },
  cardMeta: { color: C.mutedFg, fontSize: 12, marginTop: 2 },
  lastCall: { color: C.mutedFg, fontSize: 12, fontFamily: C.mono, marginTop: 10 },

  activityHeader: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  feedRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  feedBorder: { borderTopWidth: 1, borderTopColor: C.border },
  feedTool: { flex: 1, color: C.foreground, fontSize: 13, fontFamily: C.mono },
  feedTime: { color: C.mutedFg, fontSize: 10, fontFamily: C.mono },

  footerRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  clearLink: { color: C.mutedFg, fontSize: 12, textDecorationLine: 'underline' },
})
