import React, { useState } from 'react'
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity,
  TextInput, KeyboardAvoidingView, Platform, SafeAreaView, StatusBar,
} from 'react-native'
import { MOCK_SESSIONS, type Session } from '../lib/mock-data'
import { T, StateChip, TierDot, PulseDot } from '../components/ui'

function formatRelative(date: Date): string {
  const diff = Date.now() - date.getTime()
  const m = Math.floor(diff / 60000)
  const h = Math.floor(m / 60)
  if (h > 0) return `${h}h ago`
  if (m > 0) return `${m}m ago`
  return 'just now'
}

interface HomeScreenProps { navigation: any }

export function HomeScreen({ navigation }: HomeScreenProps) {
  const [input, setInput] = useState('')
  const [starting, setStarting] = useState(false)
  const sessions = MOCK_SESSIONS

  async function handleSend() {
    if (!input.trim()) return
    setStarting(true)
    setInput('')
    await new Promise(r => setTimeout(r, 900))
    setStarting(false)
    navigation.navigate('Session', { sessionId: 'sess_abc123' })
  }

  const active = sessions.filter(s => ['ACTIVE', 'PAUSED', 'STARTING'].includes(s.state))
  const past = sessions.filter(s => ['ENDED', 'FAILED'].includes(s.state))

  return (
    <SafeAreaView style={s.safe}>
      <StatusBar barStyle="light-content" />

      {/* Header */}
      <View style={s.header}>
        <View style={s.logoMark}>
          <View style={s.logoRing} />
          <View style={s.logoDot} />
        </View>
        <View style={s.headerText}>
          <Text style={s.appName}>Overwatch</Text>
          <Text style={s.appSub}>MCP Agent Control</Text>
        </View>
        {active.length > 0 && (
          <View style={s.activeBadge}>
            <PulseDot color={T.green} size={6} />
            <Text style={s.activeBadgeText}>{active.length}</Text>
          </View>
        )}
      </View>

      <FlatList
        data={sessions}
        keyExtractor={s => s.id}
        style={{ flex: 1 }}
        contentContainerStyle={s.listContent}
        ListHeaderComponent={
          <>
            {/* Active sessions */}
            {active.length > 0 && (
              <View style={s.section}>
                <Text style={s.sectionLabel}>Live now</Text>
                {active.map(session => (
                  <ActiveCard
                    key={session.id}
                    session={session}
                    onPress={() => navigation.navigate('Session', { sessionId: session.id })}
                  />
                ))}
              </View>
            )}

            {/* Past */}
            {past.length > 0 && (
              <View style={s.section}>
                <Text style={s.sectionLabel}>Recent</Text>
              </View>
            )}
          </>
        }
        renderItem={({ item }) =>
          ['ENDED', 'FAILED'].includes(item.state) ? (
            <PastCard
              session={item}
              onPress={() => navigation.navigate('Session', { sessionId: item.id })}
            />
          ) : null
        }
      />

      {/* Compose bar */}
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        style={s.composeArea}
      >
        <View style={s.compose}>
          <TextInput
            value={input}
            onChangeText={setInput}
            placeholder="Ask the agent to do something…"
            placeholderTextColor={T.textDim}
            style={s.composeInput}
            multiline
            returnKeyType="send"
            onSubmitEditing={handleSend}
          />
          <TouchableOpacity
            style={[s.sendBtn, (!input.trim() || starting) && s.sendBtnDisabled]}
            onPress={handleSend}
            disabled={!input.trim() || starting}
            activeOpacity={0.8}
          >
            {starting ? (
              <Text style={s.sendIcon}>…</Text>
            ) : (
              <Text style={s.sendIcon}>↑</Text>
            )}
          </TouchableOpacity>
        </View>
        <Text style={s.composeMeta}>
          Sandboxed · every tool call goes through the MCP gateway
        </Text>
      </KeyboardAvoidingView>
    </SafeAreaView>
  )
}

// ─── Active session card ──────────────────────────────────────────────────────
function ActiveCard({ session, onPress }: { session: Session; onPress: () => void }) {
  const lastCall = session.toolCalls[session.toolCalls.length - 1]
  const pct = (session.budget.used / session.budget.total) * 100

  return (
    <TouchableOpacity style={s.activeCard} onPress={onPress} activeOpacity={0.85}>
      {/* Glow top border */}
      <View style={[s.activeCardGlow, { opacity: session.state === 'ACTIVE' ? 1 : 0 }]} />

      <View style={s.activeCardTop}>
        <StateChip state={session.state} />
        <TierDot tier={session.bandwidthTier} />
        {session.pendingApprovals.length > 0 && (
          <View style={s.approvalPill}>
            <Text style={s.approvalPillText}>⚑ approval</Text>
          </View>
        )}
        <View style={{ flex: 1 }} />
        <Text style={s.cardTime}>{formatRelative(session.createdAt)}</Text>
      </View>

      <Text style={s.activeCardTitle} numberOfLines={2}>{session.title}</Text>

      {/* Last tool call */}
      {lastCall && (
        <View style={s.lastCall}>
          <View style={[s.lastCallDot, {
            backgroundColor: { allow: T.green, ask: T.amber, block: T.red }[lastCall.decision]
          }]} />
          <Text style={s.lastCallText} numberOfLines={1}>
            {lastCall.tool} · {lastCall.decision}
          </Text>
        </View>
      )}

      {/* Budget line */}
      <View style={s.budgetLine}>
        <View style={[s.budgetFill, {
          width: `${pct}%` as any,
          backgroundColor: pct > 75 ? T.red : pct > 50 ? T.amber : T.indigo,
        }]} />
      </View>

      {/* Footer */}
      <View style={s.activeCardFooter}>
        <Text style={s.footerStat}>
          <Text style={s.footerNum}>{session.toolCalls.length}</Text> calls
        </Text>
        <Text style={s.footerStat}>
          <Text style={[s.footerNum, { color: T.red }]}>
            {session.toolCalls.filter(t => t.decision === 'block').length}
          </Text> blocked
        </Text>
        <Text style={s.footerStat}>
          <Text style={s.footerNum}>${session.budget.costUsd.toFixed(3)}</Text>
        </Text>
        <View style={{ flex: 1 }} />
        <Text style={s.openSession}>View →</Text>
      </View>
    </TouchableOpacity>
  )
}

// ─── Past session row ─────────────────────────────────────────────────────────
function PastCard({ session, onPress }: { session: Session; onPress: () => void }) {
  const blockedCount = session.toolCalls.filter(t => t.decision === 'block').length
  return (
    <TouchableOpacity style={s.pastCard} onPress={onPress} activeOpacity={0.75}>
      <View style={[s.pastStateBar, {
        backgroundColor: session.state === 'FAILED' ? T.red : T.textFaint,
      }]} />
      <View style={s.pastContent}>
        <Text style={s.pastTitle} numberOfLines={1}>{session.title}</Text>
        <View style={s.pastMeta}>
          <Text style={s.pastMetaText}>{session.profile}</Text>
          <Text style={s.pastMetaDot}>·</Text>
          <Text style={s.pastMetaText}>{session.toolCalls.length} calls</Text>
          {blockedCount > 0 && (
            <>
              <Text style={s.pastMetaDot}>·</Text>
              <Text style={[s.pastMetaText, { color: T.red + 'aa' }]}>{blockedCount} blocked</Text>
            </>
          )}
          <Text style={s.pastMetaDot}>·</Text>
          <Text style={s.pastMetaText}>{formatRelative(session.createdAt)}</Text>
        </View>
      </View>
      <Text style={s.pastChevron}>›</Text>
    </TouchableOpacity>
  )
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: T.bg },
  listContent: { paddingBottom: 24 },

  header: {
    flexDirection: 'row', alignItems: 'center', gap: 12,
    paddingHorizontal: 24, paddingTop: 20, paddingBottom: 24,
  },
  logoMark: {
    width: 34, height: 34, alignItems: 'center', justifyContent: 'center',
  },
  logoRing: {
    position: 'absolute', width: 34, height: 34, borderRadius: 17,
    borderWidth: 2, borderColor: T.indigo + '60',
  },
  logoDot: {
    width: 12, height: 12, borderRadius: 6, backgroundColor: T.indigo,
  },
  headerText: { flex: 1 },
  appName: { color: T.text, fontSize: 20, fontWeight: '800', letterSpacing: -0.5 },
  appSub: { color: T.textDim, fontSize: 11, marginTop: 1, letterSpacing: 0.3 },
  activeBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    backgroundColor: T.greenDim, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 5,
    borderWidth: 1, borderColor: T.green + '30',
  },
  activeBadgeText: { color: T.green, fontSize: 12, fontWeight: '700' },

  section: { paddingHorizontal: 20, marginBottom: 8 },
  sectionLabel: {
    color: T.textDim, fontSize: 11, fontWeight: '700',
    letterSpacing: 1.8, textTransform: 'uppercase', marginBottom: 12,
  },

  // Active card
  activeCard: {
    backgroundColor: T.surface, borderRadius: 20,
    borderWidth: 1, borderColor: T.border,
    marginBottom: 10, overflow: 'hidden',
    padding: 16,
  },
  activeCardGlow: {
    position: 'absolute', top: 0, left: 0, right: 0, height: 1,
    backgroundColor: T.indigo,
  },
  activeCardTop: {
    flexDirection: 'row', alignItems: 'center',
    gap: 8, marginBottom: 10,
  },
  approvalPill: {
    backgroundColor: T.amberDim, borderWidth: 1, borderColor: T.amber + '40',
    borderRadius: 999, paddingHorizontal: 8, paddingVertical: 3,
  },
  approvalPillText: { color: T.amber, fontSize: 10, fontWeight: '700' },
  cardTime: { color: T.textDim, fontSize: 11 },
  activeCardTitle: {
    color: T.text, fontSize: 15, fontWeight: '500',
    lineHeight: 22, marginBottom: 10,
  },
  lastCall: {
    flexDirection: 'row', alignItems: 'center', gap: 7,
    marginBottom: 12,
  },
  lastCallDot: { width: 5, height: 5, borderRadius: 3 },
  lastCallText: { color: T.textSub, fontSize: 11, fontFamily: T.mono, flex: 1 },
  budgetLine: {
    height: 2, backgroundColor: T.border, borderRadius: 1, overflow: 'hidden', marginBottom: 10,
  },
  budgetFill: { height: 2, borderRadius: 1 },
  activeCardFooter: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  footerStat: { color: T.textDim, fontSize: 11 },
  footerNum: { color: T.textSub, fontWeight: '700', fontFamily: T.mono },
  openSession: { color: T.indigo, fontSize: 13, fontWeight: '600' },

  // Past card
  pastCard: {
    flexDirection: 'row', alignItems: 'center',
    marginHorizontal: 20, marginBottom: 2,
    paddingVertical: 12, paddingRight: 12,
    borderRadius: 14,
  },
  pastStateBar: { width: 2, height: 32, borderRadius: 1, marginRight: 12 },
  pastContent: { flex: 1 },
  pastTitle: { color: T.textSub, fontSize: 13, fontWeight: '500', marginBottom: 3 },
  pastMeta: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  pastMetaText: { color: T.textDim, fontSize: 11 },
  pastMetaDot: { color: T.textFaint, fontSize: 11 },
  pastChevron: { color: T.textFaint, fontSize: 20 },

  // Compose
  composeArea: {
    paddingHorizontal: 16, paddingBottom: 12, paddingTop: 8,
    borderTopWidth: 1, borderTopColor: T.border,
  },
  compose: {
    flexDirection: 'row', alignItems: 'flex-end',
    backgroundColor: T.surface, borderRadius: 18,
    borderWidth: 1, borderColor: T.borderMid,
    paddingLeft: 16, paddingRight: 6, paddingVertical: 6,
    marginBottom: 6,
  },
  composeInput: {
    flex: 1, color: T.text, fontSize: 15, lineHeight: 22,
    maxHeight: 100, paddingVertical: 6,
  },
  sendBtn: {
    width: 36, height: 36, borderRadius: 12,
    backgroundColor: T.indigo, alignItems: 'center', justifyContent: 'center',
    marginLeft: 6,
  },
  sendBtnDisabled: { backgroundColor: T.indigoDim },
  sendIcon: { color: '#fff', fontSize: 18, fontWeight: '700', marginTop: -2 },
  composeMeta: {
    color: T.textFaint, fontSize: 10, textAlign: 'center', letterSpacing: 0.3,
  },
})
