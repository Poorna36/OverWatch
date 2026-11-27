import React, { useState } from 'react'
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, StatusBar,
} from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import { MOCK_SESSIONS, type EnvConfig, type ChatThread, type Session } from '../lib/mock-data'
import { T, StateChip, DecisionPill, RiskTag, PulseDot } from '../components/ui'

interface HomeScreenProps {
  navigation: any
  env: EnvConfig
  chats: ChatThread[]
  onOpenChat: (chatId: string | null) => void
}

export function HomeScreen({ navigation, env, chats, onOpenChat }: HomeScreenProps) {
  const [sessions, setSessions] = useState<Session[]>(MOCK_SESSIONS)
  const [logFilter, setLogFilter] = useState<'all' | 'allow' | 'block'>('all')

  const activeSessions = sessions.filter(s => ['ACTIVE', 'PAUSED', 'STARTING'].includes(s.state))
  const approvals = sessions.flatMap(s => s.pendingApprovals.map(a => ({ session: s, approval: a })))
  const allLogs = sessions.flatMap(s =>
    s.toolCalls.map(tc => ({ ...tc, sessionId: s.id, profile: s.profile }))
  ).sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime())

  const visibleLogs = logFilter === 'all' ? allLogs : allLogs.filter(l => l.decision === logFilter)
  const blockedCount = allLogs.filter(l => l.decision === 'block').length

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
          <View>
            <Text style={s.heading}>Home & Activity</Text>
            <Text style={s.sub}>Tasks, approvals & gateway audit logs</Text>
          </View>
          <TouchableOpacity
            style={s.newChatChip}
            onPress={() => onOpenChat(null)}
            activeOpacity={0.85}
          >
            <Text style={s.newChatChipText}>+ New Chat</Text>
          </TouchableOpacity>
        </View>

        {/* Connected Environment Card */}
        <View style={s.envCard}>
          <View style={s.envTop}>
            <View style={s.envBadge}>
              <PulseDot color={env.mode === 'desktop' ? T.green : T.indigo} size={7} />
              <Text style={s.envBadgeText}>
                {env.mode === 'desktop' ? 'CONNECTED TO DESKTOP' : 'VM SANDBOX READY'}
              </Text>
            </View>
            <TouchableOpacity onPress={() => navigation.navigate('SettingsTab')}>
              <Text style={s.switchLink}>Switch →</Text>
            </TouchableOpacity>
          </View>
          <Text style={s.envHost}>
            {env.mode === 'desktop' ? env.desktopHost : env.vmImage}
          </Text>
          <Text style={s.envMeta}>
            {env.mode === 'desktop'
              ? 'Remote Desktop bridge · Calendar, Browser & Python workspace'
              : `${env.vmDeps.length} deps · ${env.vmMcpAgents.length} MCP agents`}
          </Text>
        </View>

        {/* Stats Strip */}
        <View style={s.statsRow}>
          <View style={s.statBox}>
            <Text style={[s.statNum, { color: T.green }]}>{activeSessions.length}</Text>
            <Text style={s.statLabel}>Running</Text>
          </View>
          <View style={s.statBox}>
            <Text style={[s.statNum, { color: T.amber }]}>{approvals.length}</Text>
            <Text style={s.statLabel}>Approvals</Text>
          </View>
          <View style={s.statBox}>
            <Text style={[s.statNum, { color: T.red }]}>{blockedCount}</Text>
            <Text style={s.statLabel}>Blocked</Text>
          </View>
        </View>

        {/* Pending Approvals */}
        {approvals.length > 0 && (
          <>
            <Text style={s.sectionLabel}>NEEDS APPROVAL</Text>
            <View style={s.list}>
              {approvals.map(({ session, approval }) => (
                <View key={approval.id} style={s.approvalCard}>
                  <View style={s.sessionTop}>
                    <Text style={s.warnTag}>PERMISSION</Text>
                    <RiskTag tier={approval.riskTier} />
                  </View>
                  <Text style={s.logTool}>{approval.tool}</Text>
                  <Text style={s.sessionTitle}>{approval.summary || session.title}</Text>
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
              ))}
            </View>
          </>
        )}

        {/* Running Sessions */}
        <Text style={s.sectionLabel}>RUNNING SESSIONS</Text>
        <View style={s.list}>
          {MOCK_SESSIONS.map(sess => (
            <TouchableOpacity
              key={sess.id}
              style={s.sessionCard}
              onPress={() => navigation.navigate('Session', { sessionId: sess.id })}
              activeOpacity={0.8}
            >
              <View style={s.sessionTop}>
                <StateChip state={sess.state} />
                <Text style={s.profileTag}>{sess.profile}</Text>
                <View style={{ flex: 1 }} />
                <Text style={s.viewLink}>Inspect →</Text>
              </View>
              <Text style={s.sessionTitle} numberOfLines={2}>{sess.title}</Text>
              <Text style={s.sessionMeta}>
                {sess.toolCalls.length} calls · ${sess.budget.costUsd.toFixed(3)}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* Gateway Audit Logs */}
        <View style={s.logHeaderRow}>
          <Text style={s.sectionLabel}>AUDIT & GATEWAY LOGS</Text>
          <View style={s.filterChips}>
            {(['all', 'allow', 'block'] as const).map(f => (
              <TouchableOpacity
                key={f}
                style={[s.miniChip, logFilter === f && s.miniChipActive]}
                onPress={() => setLogFilter(f)}
              >
                <Text style={[s.miniChipText, logFilter === f && { color: T.indigo }]}>{f}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>
        <View style={s.list}>
          {visibleLogs.slice(0, 6).map(log => (
            <View
              key={`${log.sessionId}_${log.id}`}
              style={[s.logRow, log.decision === 'block' && { borderColor: T.red + '44' }]}
            >
              <View style={s.logTop}>
                <Text style={s.logTool}>{log.tool}</Text>
                <DecisionPill decision={log.decision} />
              </View>
              {log.reason && <Text style={s.logReason}>{log.reason}</Text>}
              <Text style={s.logArgs} numberOfLines={1}>{JSON.stringify(log.args)}</Text>
            </View>
          ))}
        </View>

        {/* Recent Chats shortcut */}
        <Text style={s.sectionLabel}>RECENT CHATS</Text>
        <View style={s.list}>
          {chats.slice(0, 3).map(c => (
            <TouchableOpacity
              key={c.id}
              style={s.chatRow}
              onPress={() => onOpenChat(c.id)}
              activeOpacity={0.8}
            >
              <View style={{ flex: 1 }}>
                <Text style={s.chatTitle} numberOfLines={1}>{c.title}</Text>
                <Text style={s.chatMeta}>{c.role} · {c.messages.length} messages</Text>
              </View>
              <Text style={s.chevron}>›</Text>
            </TouchableOpacity>
          ))}
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: T.bg },
  content: { padding: 16, paddingBottom: 40 },

  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    marginBottom: 16, marginTop: 4,
  },
  heading: { color: T.text, fontSize: 22, fontWeight: '700', letterSpacing: -0.4 },
  sub: { color: T.textDim, fontSize: 12, marginTop: 2 },
  newChatChip: {
    backgroundColor: T.indigo, paddingHorizontal: 14, paddingVertical: 8, borderRadius: 12,
  },
  newChatChipText: { color: '#141413', fontSize: 12, fontWeight: '700' },

  envCard: {
    backgroundColor: T.surface, borderRadius: 18,
    borderWidth: 1, borderColor: T.borderMid, padding: 15, marginBottom: 12,
  },
  envTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  envBadge: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  envBadgeText: { color: T.textSub, fontSize: 10, fontWeight: '700', letterSpacing: 0.8 },
  switchLink: { color: T.indigo, fontSize: 12, fontWeight: '600' },
  envHost: { color: T.text, fontSize: 15, fontWeight: '700', marginBottom: 3 },
  envMeta: { color: T.textDim, fontSize: 12, lineHeight: 17 },

  statsRow: { flexDirection: 'row', gap: 10, marginBottom: 20 },
  statBox: {
    flex: 1, backgroundColor: T.surface, borderRadius: 14,
    borderWidth: 1, borderColor: T.border, paddingVertical: 12, alignItems: 'center',
  },
  statNum: { fontSize: 20, fontWeight: '700', fontFamily: T.mono },
  statLabel: { color: T.textDim, fontSize: 11, marginTop: 2 },

  sectionLabel: {
    color: T.textDim, fontSize: 10, fontWeight: '700', letterSpacing: 1,
    marginBottom: 10, marginTop: 2,
  },
  list: { gap: 10, marginBottom: 20 },

  approvalCard: {
    backgroundColor: T.surface, borderRadius: 16,
    borderWidth: 1, borderColor: T.amber + '66', padding: 14, gap: 7,
  },
  warnTag: {
    color: T.amber, fontSize: 9, fontWeight: '800', letterSpacing: 0.6,
    backgroundColor: T.amberDim, paddingHorizontal: 7, paddingVertical: 3, borderRadius: 6,
  },
  btnRow: { flexDirection: 'row', gap: 8, marginTop: 4 },
  denyBtn: {
    flex: 1, backgroundColor: T.redDim, borderWidth: 1, borderColor: T.red + '44',
    paddingVertical: 9, borderRadius: 10, alignItems: 'center',
  },
  denyText: { color: T.red, fontSize: 12, fontWeight: '700' },
  approveBtn: {
    flex: 2, backgroundColor: T.indigo, paddingVertical: 9, borderRadius: 10, alignItems: 'center',
  },
  approveText: { color: '#141413', fontSize: 12, fontWeight: '700' },

  sessionCard: {
    backgroundColor: T.surface, borderRadius: 16,
    borderWidth: 1, borderColor: T.border, padding: 14, gap: 7,
  },
  sessionTop: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  profileTag: { color: T.textDim, fontSize: 11, fontFamily: T.mono },
  viewLink: { color: T.indigo, fontSize: 12, fontWeight: '600' },
  sessionTitle: { color: T.text, fontSize: 14, fontWeight: '600', lineHeight: 20 },
  sessionMeta: { color: T.textDim, fontSize: 11, fontFamily: T.mono },

  logHeaderRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  filterChips: { flexDirection: 'row', gap: 6 },
  miniChip: {
    paddingHorizontal: 9, paddingVertical: 4, borderRadius: 8,
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
  },
  miniChipActive: { borderColor: T.indigo, backgroundColor: T.indigoDim },
  miniChipText: { color: T.textDim, fontSize: 11, fontFamily: T.mono },

  logRow: {
    backgroundColor: T.surface, borderRadius: 14,
    borderWidth: 1, borderColor: T.border, padding: 12, gap: 4,
  },
  logTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  logTool: { color: T.text, fontSize: 13, fontWeight: '700', fontFamily: T.mono },
  logReason: { color: T.red, fontSize: 11 },
  logArgs: { color: T.textDim, fontSize: 11, fontFamily: T.mono },

  chatRow: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    backgroundColor: T.surface, borderRadius: 14,
    borderWidth: 1, borderColor: T.border, padding: 14,
  },
  chatTitle: { color: T.text, fontSize: 14, fontWeight: '600' },
  chatMeta: { color: T.textDim, fontSize: 11, marginTop: 2 },
  chevron: { color: T.textDim, fontSize: 18 },
})
