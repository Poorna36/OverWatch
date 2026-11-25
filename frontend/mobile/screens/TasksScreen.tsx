import React, { useState } from 'react'
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, StatusBar,
} from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import { MOCK_SESSIONS, type Session, type ToolCallEvent } from '../lib/mock-data'
import { T, StateChip, DecisionPill, RiskTag, PulseDot } from '../components/ui'

type TabFilter = 'all' | 'running' | 'blocked' | 'approvals'

export function TasksScreen({ navigation }: { navigation: any }) {
  const [filter, setFilter] = useState<TabFilter>('all')
  const [sessions, setSessions] = useState<Session[]>(MOCK_SESSIONS)

  const running = sessions.filter(s => ['ACTIVE', 'PAUSED', 'STARTING'].includes(s.state))
  const approvals = sessions.flatMap(s =>
    s.pendingApprovals.map(a => ({ session: s, approval: a }))
  )
  const blockedCalls: Array<{ session: Session; call: ToolCallEvent }> = sessions.flatMap(s =>
    s.toolCalls.filter(t => t.decision === 'block').map(call => ({ session: s, call }))
  )

  function handleApprove(sessionId: string, approvalId: string) {
    setSessions(prev =>
      prev.map(s =>
        s.id !== sessionId
          ? s
          : {
            ...s,
            pendingApprovals: s.pendingApprovals.filter(a => a.id !== approvalId),
            toolCalls: s.toolCalls.map(tc =>
              tc.decision === 'ask' ? { ...tc, decision: 'allow', reason: 'Approved by user' } : tc
            ),
          }
      )
    )
  }

  function handleDeny(sessionId: string, approvalId: string) {
    setSessions(prev =>
      prev.map(s =>
        s.id !== sessionId
          ? s
          : {
            ...s,
            pendingApprovals: s.pendingApprovals.filter(a => a.id !== approvalId),
            toolCalls: s.toolCalls.map(tc =>
              tc.decision === 'ask' ? { ...tc, decision: 'block', reason: 'Denied by user' } : tc
            ),
          }
      )
    )
  }

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <StatusBar barStyle="light-content" />
      <ScrollView contentContainerStyle={s.content} showsVerticalScrollIndicator={false}>
        {/* Header */}
        <View style={s.header}>
          <Text style={s.heading}>Tasks & Permissions</Text>
          <Text style={s.sub}>
            {running.length} running · {approvals.length} pending · {blockedCalls.length} blocked
          </Text>
        </View>

        {/* Filter Chips */}
        <View style={s.filterRow}>
          {([
            { id: 'all', label: 'All' },
            { id: 'running', label: `Running (${running.length})` },
            { id: 'approvals', label: `Pending (${approvals.length})` },
            { id: 'blocked', label: `Blocked (${blockedCalls.length})` },
          ] as const).map(f => (
            <TouchableOpacity
              key={f.id}
              style={[s.filterChip, filter === f.id && s.filterChipActive]}
              onPress={() => setFilter(f.id)}
              activeOpacity={0.8}
            >
              <Text style={[s.filterText, filter === f.id && s.filterTextActive]}>
                {f.label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* Pending Approvals */}
        {(filter === 'all' || filter === 'approvals') && (
          <View style={s.section}>
            <Text style={s.sectionLabel}>NEEDS APPROVAL</Text>
            {approvals.length === 0 ? (
              <View style={s.emptyCard}>
                <Text style={s.emptyText}>No pending permission requests</Text>
              </View>
            ) : (
              approvals.map(({ session, approval }) => (
                <View key={approval.id} style={s.approvalCard}>
                  <View style={s.cardTop}>
                    <View style={s.warnBadge}>
                      <Text style={s.warnBadgeText}>PERMISSION REQUIRED</Text>
                    </View>
                    <RiskTag tier={approval.riskTier} />
                  </View>
                  <Text style={s.toolName}>{approval.tool}</Text>
                  <Text style={s.summary}>{approval.summary || session.title}</Text>
                  <Text style={s.argsText} numberOfLines={2}>
                    {JSON.stringify(approval.args)}
                  </Text>
                  <View style={s.btnRow}>
                    <TouchableOpacity
                      style={s.denyBtn}
                      onPress={() => handleDeny(session.id, approval.id)}
                    >
                      <Text style={s.denyText}>Block</Text>
                    </TouchableOpacity>
                    <TouchableOpacity
                      style={s.approveBtn}
                      onPress={() => handleApprove(session.id, approval.id)}
                    >
                      <Text style={s.approveText}>Allow Once</Text>
                    </TouchableOpacity>
                  </View>
                </View>
              ))
            )}
          </View>
        )}

        {/* Running Tasks */}
        {(filter === 'all' || filter === 'running') && (
          <View style={s.section}>
            <Text style={s.sectionLabel}>RUNNING TASKS</Text>
            {running.map(sess => (
              <TouchableOpacity
                key={sess.id}
                style={s.taskCard}
                onPress={() => navigation.navigate('Session', { sessionId: sess.id })}
                activeOpacity={0.8}
              >
                <View style={s.cardTop}>
                  <View style={s.runBadge}>
                    <PulseDot color={T.green} size={6} />
                    <Text style={s.runBadgeText}>RUNNING</Text>
                  </View>
                  <StateChip state={sess.state} />
                </View>
                <Text style={s.taskTitle}>{sess.title}</Text>
                <Text style={s.taskMeta}>
                  {sess.profile} · {sess.toolCalls.length} calls · {sess.budget.used}/{sess.budget.total} budget
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        )}

        {/* Blocked Actions */}
        {(filter === 'all' || filter === 'blocked') && (
          <View style={s.section}>
            <Text style={s.sectionLabel}>BLOCKED BY GATEWAY</Text>
            {blockedCalls.map(({ session, call }) => (
              <View key={`${session.id}_${call.id}`} style={s.blockedCard}>
                <View style={s.cardTop}>
                  <DecisionPill decision="block" />
                  <RiskTag tier={call.riskTier} />
                  <View style={{ flex: 1 }} />
                  <Text style={s.sessionRef}>{session.profile}</Text>
                </View>
                <Text style={s.toolName}>{call.tool}</Text>
                <Text style={s.blockedReason}>{call.reason ?? 'Blocked by MCP policy'}</Text>
                <Text style={s.argsText} numberOfLines={2}>
                  {JSON.stringify(call.args)}
                </Text>
              </View>
            ))}
          </View>
        )}
      </ScrollView>
    </SafeAreaView>
  )
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: T.bg },
  content: { padding: 16, paddingBottom: 40 },

  header: { marginBottom: 16, marginTop: 4 },
  heading: { color: T.text, fontSize: 24, fontWeight: '700', letterSpacing: -0.4 },
  sub: { color: T.textDim, fontSize: 12, marginTop: 2 },

  filterRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 20 },
  filterChip: {
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
    paddingHorizontal: 11, paddingVertical: 6, borderRadius: 10,
  },
  filterChipActive: { backgroundColor: T.indigoDim, borderColor: T.indigo },
  filterText: { color: T.textSub, fontSize: 12, fontWeight: '600' },
  filterTextActive: { color: T.indigo },

  section: { marginBottom: 22, gap: 10 },
  sectionLabel: { color: T.textDim, fontSize: 10, fontWeight: '700', letterSpacing: 1 },

  emptyCard: {
    backgroundColor: T.surface, borderRadius: 14,
    borderWidth: 1, borderColor: T.border, padding: 16, alignItems: 'center',
  },
  emptyText: { color: T.textDim, fontSize: 12 },

  approvalCard: {
    backgroundColor: T.surface, borderRadius: 16,
    borderWidth: 1, borderColor: T.amber + '66', padding: 14, gap: 8,
  },
  cardTop: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  warnBadge: {
    backgroundColor: T.amberDim, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6,
  },
  warnBadgeText: { color: T.amber, fontSize: 9, fontWeight: '800', letterSpacing: 0.6 },
  toolName: { color: T.text, fontSize: 14, fontWeight: '700', fontFamily: T.mono },
  summary: { color: T.textSub, fontSize: 13 },
  argsText: {
    color: T.textDim, fontSize: 11, fontFamily: T.mono,
    backgroundColor: T.elevated, padding: 8, borderRadius: 8,
  },
  btnRow: { flexDirection: 'row', gap: 8, marginTop: 4 },
  denyBtn: {
    flex: 1, backgroundColor: T.redDim, borderWidth: 1, borderColor: T.red + '44',
    paddingVertical: 10, borderRadius: 10, alignItems: 'center',
  },
  denyText: { color: T.red, fontSize: 12, fontWeight: '700' },
  approveBtn: {
    flex: 2, backgroundColor: T.indigo, paddingVertical: 10, borderRadius: 10, alignItems: 'center',
  },
  approveText: { color: '#141413', fontSize: 12, fontWeight: '700' },

  taskCard: {
    backgroundColor: T.surface, borderRadius: 16,
    borderWidth: 1, borderColor: T.border, padding: 14, gap: 7,
  },
  runBadge: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  runBadgeText: { color: T.green, fontSize: 10, fontWeight: '800', letterSpacing: 0.6 },
  taskTitle: { color: T.text, fontSize: 14, fontWeight: '600' },
  taskMeta: { color: T.textDim, fontSize: 11, fontFamily: T.mono },

  blockedCard: {
    backgroundColor: T.surface, borderRadius: 16,
    borderWidth: 1, borderColor: T.red + '44', padding: 14, gap: 7,
  },
  sessionRef: { color: T.textDim, fontSize: 11, fontFamily: T.mono },
  blockedReason: { color: T.red, fontSize: 12, fontWeight: '500' },
})
