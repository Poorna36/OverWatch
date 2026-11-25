import React from 'react'
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity,
  SafeAreaView, StatusBar,
} from 'react-native'
import { MOCK_SESSIONS, type Session } from '../lib/mock-data'
import { StatusDot, TierBadge, BudgetBar, C } from '../components/ui'

const STATE_LABEL: Record<Session['state'], string> = {
  CREATED: 'Created', PLANNING: 'Planning', STARTING: 'Starting',
  ACTIVE: 'Active', PAUSED: 'Paused', ENDING: 'Ending',
  ENDED: 'Ended', FAILED: 'Failed',
}

interface HomeScreenProps {
  navigation: any
}

export function HomeScreen({ navigation }: HomeScreenProps) {
  const sessions = MOCK_SESSIONS
  const active = sessions.filter(s => ['ACTIVE', 'PAUSED', 'STARTING'].includes(s.state))
  const past = sessions.filter(s => ['ENDED', 'FAILED'].includes(s.state))

  return (
    <SafeAreaView style={styles.safe}>
      <StatusBar barStyle="light-content" backgroundColor={C.bg} />
      <FlatList
        data={sessions}
        keyExtractor={s => s.id}
        style={styles.list}
        contentContainerStyle={styles.content}
        ListHeaderComponent={
          <View>
            {/* Header */}
            <View style={styles.header}>
              <View style={styles.logoRow}>
                <View style={styles.logoBox}>
                  <Text style={styles.logoIcon}>◉</Text>
                </View>
                <View>
                  <Text style={styles.logoTitle}>Overwatch</Text>
                  <Text style={styles.logoSub}>MCP Control Plane</Text>
                </View>
              </View>
              <TouchableOpacity
                style={styles.newBtn}
                onPress={() => navigation.navigate('NewTask')}
                activeOpacity={0.8}
              >
                <Text style={styles.newBtnText}>+ New task</Text>
              </TouchableOpacity>
            </View>

            {/* Stats */}
            <View style={styles.statsRow}>
              {[
                { label: 'Sessions', value: sessions.length.toString() },
                { label: 'Tool calls', value: sessions.reduce((a, s) => a + s.toolCalls.length, 0).toString() },
                { label: 'Blocked', value: sessions.reduce((a, s) => a + s.toolCalls.filter(t => t.decision === 'block').length, 0).toString() },
              ].map(stat => (
                <View key={stat.label} style={styles.statCard}>
                  <Text style={styles.statValue}>{stat.value}</Text>
                  <Text style={styles.statLabel}>{stat.label}</Text>
                </View>
              ))}
            </View>

            {active.length > 0 && <Text style={styles.sectionTitle}>ACTIVE</Text>}
            {active.map(s => (
              <SessionCard key={s.id} session={s} onPress={() => navigation.navigate('Session', { sessionId: s.id })} />
            ))}

            {past.length > 0 && <Text style={[styles.sectionTitle, { marginTop: 20 }]}>RECENT</Text>}
          </View>
        }
        renderItem={({ item }) =>
          ['ENDED', 'FAILED'].includes(item.state)
            ? <SessionCard key={item.id} session={item} onPress={() => navigation.navigate('Session', { sessionId: item.id })} />
            : null
        }
      />
    </SafeAreaView>
  )
}

function SessionCard({ session: s, onPress }: { session: Session; onPress: () => void }) {
  const pct = (s.budget.used / s.budget.total) * 100
  const fillColor = pct > 80 ? C.red : pct > 60 ? C.amber : C.indigo

  return (
    <TouchableOpacity style={styles.card} onPress={onPress} activeOpacity={0.8}>
      <View style={styles.cardRow}>
        <StatusDot state={s.state} size={8} />
        <View style={styles.cardMid}>
          <Text style={styles.cardTitle} numberOfLines={2}>{s.title}</Text>
          <Text style={styles.cardMeta}>
            {STATE_LABEL[s.state]} · {s.profile} · {s.toolCalls.length} calls
            {s.toolCalls.filter(t => t.decision === 'block').length > 0
              ? ` · ${s.toolCalls.filter(t => t.decision === 'block').length} blocked`
              : ''}
          </Text>
        </View>
        <TierBadge tier={s.bandwidthTier} />
      </View>

      {/* Pending approval indicator */}
      {s.pendingApprovals.length > 0 && (
        <View style={styles.pendingBanner}>
          <Text style={styles.pendingText}>⚠ {s.pendingApprovals.length} approval pending</Text>
        </View>
      )}

      {/* Budget strip */}
      <View style={styles.budgetStrip}>
        <View style={[styles.budgetFill, { width: `${pct}%` as any, backgroundColor: fillColor }]} />
      </View>
    </TouchableOpacity>
  )
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.bg },
  list: { flex: 1 },
  content: { paddingBottom: 40 },

  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 20, paddingTop: 20, paddingBottom: 16,
  },
  logoRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  logoBox: {
    width: 36, height: 36, borderRadius: 10,
    backgroundColor: C.indigo, alignItems: 'center', justifyContent: 'center',
  },
  logoIcon: { color: '#fff', fontSize: 16 },
  logoTitle: { color: C.text, fontSize: 18, fontWeight: '700', letterSpacing: -0.5 },
  logoSub: { color: C.textMuted, fontSize: 10, marginTop: 1 },
  newBtn: {
    backgroundColor: C.indigo, paddingHorizontal: 14, paddingVertical: 8,
    borderRadius: 10,
  },
  newBtnText: { color: '#fff', fontSize: 13, fontWeight: '600' },

  statsRow: {
    flexDirection: 'row', gap: 10, paddingHorizontal: 20, paddingBottom: 20,
  },
  statCard: {
    flex: 1, backgroundColor: C.surface, borderWidth: 1, borderColor: C.border,
    borderRadius: 12, padding: 12, alignItems: 'center',
  },
  statValue: { color: C.text, fontSize: 22, fontWeight: '700', fontFamily: 'monospace' },
  statLabel: { color: C.textMuted, fontSize: 10, marginTop: 2 },

  sectionTitle: {
    color: C.textMuted, fontSize: 10, fontWeight: '700', letterSpacing: 1.5,
    paddingHorizontal: 20, paddingBottom: 8,
  },

  card: {
    marginHorizontal: 16, marginBottom: 8, backgroundColor: C.surface,
    borderWidth: 1, borderColor: C.border, borderRadius: 14, padding: 14,
    overflow: 'hidden',
  },
  cardRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
  cardMid: { flex: 1 },
  cardTitle: { color: C.text, fontSize: 13, fontWeight: '500', lineHeight: 18, marginBottom: 4 },
  cardMeta: { color: C.textMuted, fontSize: 11, fontFamily: 'monospace' },

  pendingBanner: {
    marginTop: 8, backgroundColor: '#451a0322', borderWidth: 1,
    borderColor: '#fbbf2440', borderRadius: 8, paddingHorizontal: 10, paddingVertical: 5,
  },
  pendingText: { color: C.amber, fontSize: 11, fontWeight: '600' },

  budgetStrip: { height: 2, backgroundColor: C.surface2, borderRadius: 1, marginTop: 10, overflow: 'hidden' },
  budgetFill: { height: 2, borderRadius: 1 },
})
