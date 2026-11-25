import React from 'react'
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, SafeAreaView, StatusBar,
} from 'react-native'
import { MOCK_SESSIONS, type EnvConfig, type ChatThread } from '../lib/mock-data'
import { T, StateChip, PulseDot } from '../components/ui'

interface HomeScreenProps {
  navigation: any
  env: EnvConfig
  chats: ChatThread[]
  onOpenChat: (chatId: string | null) => void
}

export function HomeScreen({ navigation, env, chats, onOpenChat }: HomeScreenProps) {
  const activeSessions = MOCK_SESSIONS.filter(s => ['ACTIVE', 'PAUSED', 'STARTING'].includes(s.state))
  const blockedCount = MOCK_SESSIONS.reduce(
    (acc, s) => acc + s.toolCalls.filter(t => t.decision === 'block').length,
    0,
  )
  const pendingCount = MOCK_SESSIONS.reduce((acc, s) => acc + s.pendingApprovals.length, 0)

  return (
    <SafeAreaView style={s.safe}>
      <StatusBar barStyle="light-content" />
      <ScrollView contentContainerStyle={s.content} showsVerticalScrollIndicator={false}>
        {/* Header */}
        <View style={s.header}>
          <View>
            <Text style={s.heading}>Home</Text>
            <Text style={s.sub}>Workspace & live control plane</Text>
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
              ? 'Remote Desktop bridge · Local calendar, browser & Python workspace'
              : `${env.vmDeps.length} deps · ${env.vmMcpAgents.length} MCP agents · ${env.vmIncludes.length} mounts`}
          </Text>
        </View>

        {/* Quick Status Row */}
        <View style={s.statsRow}>
          <TouchableOpacity style={s.statBox} onPress={() => navigation.navigate('TasksTab')} activeOpacity={0.8}>
            <Text style={[s.statNum, { color: T.green }]}>{activeSessions.length}</Text>
            <Text style={s.statLabel}>Running</Text>
          </TouchableOpacity>
          <TouchableOpacity style={s.statBox} onPress={() => navigation.navigate('TasksTab')} activeOpacity={0.8}>
            <Text style={[s.statNum, { color: T.amber }]}>{pendingCount}</Text>
            <Text style={s.statLabel}>Approvals</Text>
          </TouchableOpacity>
          <TouchableOpacity style={s.statBox} onPress={() => navigation.navigate('TasksTab')} activeOpacity={0.8}>
            <Text style={[s.statNum, { color: T.red }]}>{blockedCount}</Text>
            <Text style={s.statLabel}>Blocked</Text>
          </TouchableOpacity>
        </View>

        {/* Live Sessions */}
        <Text style={s.sectionLabel}>LIVE SESSIONS</Text>
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
                {sess.toolCalls.length} tool calls · ${sess.budget.costUsd.toFixed(3)}
              </Text>
            </TouchableOpacity>
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
    marginBottom: 18, marginTop: 4,
  },
  heading: { color: T.text, fontSize: 24, fontWeight: '700', letterSpacing: -0.4 },
  sub: { color: T.textDim, fontSize: 12, marginTop: 2 },
  newChatChip: {
    backgroundColor: T.indigo, paddingHorizontal: 14, paddingVertical: 8, borderRadius: 12,
  },
  newChatChipText: { color: '#141413', fontSize: 12, fontWeight: '700' },

  envCard: {
    backgroundColor: T.surface, borderRadius: 18,
    borderWidth: 1, borderColor: T.borderMid, padding: 16, marginBottom: 14,
  },
  envTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 },
  envBadge: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  envBadgeText: { color: T.textSub, fontSize: 10, fontWeight: '700', letterSpacing: 0.8 },
  switchLink: { color: T.indigo, fontSize: 12, fontWeight: '600' },
  envHost: { color: T.text, fontSize: 16, fontWeight: '700', marginBottom: 4 },
  envMeta: { color: T.textDim, fontSize: 12, lineHeight: 17 },

  statsRow: { flexDirection: 'row', gap: 10, marginBottom: 22 },
  statBox: {
    flex: 1, backgroundColor: T.surface, borderRadius: 14,
    borderWidth: 1, borderColor: T.border, paddingVertical: 14, alignItems: 'center',
  },
  statNum: { fontSize: 22, fontWeight: '700', fontFamily: T.mono },
  statLabel: { color: T.textDim, fontSize: 11, marginTop: 2 },

  sectionLabel: {
    color: T.textDim, fontSize: 10, fontWeight: '700', letterSpacing: 1,
    marginBottom: 10, marginTop: 4,
  },
  list: { gap: 10, marginBottom: 20 },

  sessionCard: {
    backgroundColor: T.surface, borderRadius: 16,
    borderWidth: 1, borderColor: T.border, padding: 14, gap: 8,
  },
  sessionTop: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  profileTag: { color: T.textDim, fontSize: 11, fontFamily: T.mono },
  viewLink: { color: T.indigo, fontSize: 12, fontWeight: '600' },
  sessionTitle: { color: T.text, fontSize: 14, fontWeight: '600', lineHeight: 20 },
  sessionMeta: { color: T.textDim, fontSize: 11, fontFamily: T.mono },

  chatRow: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    backgroundColor: T.surface, borderRadius: 14,
    borderWidth: 1, borderColor: T.border, padding: 14,
  },
  chatTitle: { color: T.text, fontSize: 14, fontWeight: '600' },
  chatMeta: { color: T.textDim, fontSize: 11, marginTop: 2 },
  chevron: { color: T.textDim, fontSize: 18 },
})
