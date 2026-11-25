import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  SafeAreaView, StatusBar, Animated, Modal,
} from 'react-native'
import { MOCK_SESSIONS, DEMO_EVENTS, type Session, type ToolCallEvent, type ApprovalRequest } from '../lib/mock-data'
import { StatusDot, TierBadge, BudgetBar, DecisionBadge, RiskBadge, C } from '../components/ui'

function formatTime(d: Date) {
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

interface SessionScreenProps { navigation: any; route: any }

export function SessionScreen({ navigation, route }: SessionScreenProps) {
  const { sessionId } = route.params ?? { sessionId: 'sess_abc123' }
  const seed = MOCK_SESSIONS.find(s => s.id === sessionId) ?? MOCK_SESSIONS[0]

  const [session, setSession] = useState<Session>({ ...seed, toolCalls: [...seed.toolCalls] })
  const [demoRunning, setDemoRunning] = useState(false)
  const [showApproval, setShowApproval] = useState(false)
  const feedRef = useRef<ScrollView>(null)

  useEffect(() => {
    if (session.pendingApprovals.length > 0) setShowApproval(true)
  }, [session.pendingApprovals.length])

  async function runDemo() {
    setDemoRunning(true)
    setSession(s => ({ ...s, toolCalls: [], state: 'STARTING', pendingApprovals: [] }))
    await new Promise(r => setTimeout(r, 1000))
    setSession(s => ({ ...s, state: 'ACTIVE' }))

    let seq = 1
    for (const { delay, event } of DEMO_EVENTS) {
      await new Promise(r => setTimeout(r, delay))
      const newCall: ToolCallEvent = { ...event, id: `demo_${seq}`, seq, timestamp: new Date() }
      setSession(s => ({
        ...s,
        toolCalls: [...s.toolCalls, newCall],
        budget: { ...s.budget, used: Math.min(s.budget.used + 6, s.budget.total) },
        pendingApprovals: event.decision === 'ask'
          ? [...s.pendingApprovals, {
              id: `apr_demo_${seq}`, tool: event.tool, args: event.args,
              riskTier: event.riskTier, reversible: event.reversible,
              createdAt: new Date(), expiresAt: new Date(Date.now() + 120_000),
            } as ApprovalRequest]
          : s.pendingApprovals,
      }))
      seq++
      setTimeout(() => feedRef.current?.scrollToEnd({ animated: true }), 100)
    }
    setDemoRunning(false)
  }

  const handleApprove = useCallback((aid: string) => {
    setSession(s => ({ ...s, pendingApprovals: s.pendingApprovals.filter(a => a.id !== aid) }))
    setShowApproval(false)
  }, [])

  const handleDeny = useCallback((aid: string) => {
    setSession(s => ({ ...s, pendingApprovals: s.pendingApprovals.filter(a => a.id !== aid) }))
    setShowApproval(false)
  }, [])

  const approval = session.pendingApprovals[0]

  return (
    <SafeAreaView style={ss.safe}>
      <StatusBar barStyle="light-content" backgroundColor={C.bg} />

      {/* Top bar */}
      <View style={ss.topBar}>
        <TouchableOpacity onPress={() => navigation.goBack()} style={ss.backBtn}>
          <Text style={ss.backText}>←</Text>
        </TouchableOpacity>
        <StatusDot state={session.state} size={8} />
        <Text style={ss.stateName}>{session.state}</Text>
        <TierBadge tier={session.bandwidthTier} />
        <Text style={ss.profileTag}>{session.profile}</Text>
        <View style={{ flex: 1 }} />
        {!['ENDED', 'FAILED'].includes(session.state) && (
          <TouchableOpacity
            style={ss.demoBtn}
            onPress={runDemo}
            disabled={demoRunning}
            activeOpacity={0.8}
          >
            <Text style={ss.demoBtnText}>{demoRunning ? '…' : '▶ Demo'}</Text>
          </TouchableOpacity>
        )}
      </View>

      {/* Task title */}
      <View style={ss.titleStrip}>
        <Text style={ss.title} numberOfLines={2}>{session.title}</Text>
      </View>

      <ScrollView style={ss.scroll} contentContainerStyle={ss.content}>

        {/* Live view mock */}
        <View style={ss.liveViewBox}>
          <View style={ss.liveViewTitle}>
            <View style={ss.trafficLights}>
              <View style={[ss.tl, { backgroundColor: '#f8717160' }]} />
              <View style={[ss.tl, { backgroundColor: '#fbbf2460' }]} />
              <View style={[ss.tl, { backgroundColor: '#34d39960' }]} />
            </View>
            <Text style={ss.liveViewLabel}>Terminal — /workspace</Text>
            <View style={ss.liveDot}>
              <Text style={ss.liveDotText}>● LIVE</Text>
            </View>
          </View>
          <Text style={ss.liveViewCode}>
            {session.toolCalls.length === 0
              ? '$ waiting for agent…'
              : `$ ${(session.toolCalls[session.toolCalls.length - 1]?.args?.cmd as string | undefined) ?? (session.toolCalls[session.toolCalls.length - 1]?.tool) ?? '…'}`}
            {'\n'}<Text style={{ color: C.textMuted }}>_</Text>
          </Text>
        </View>

        {/* Budget */}
        <View style={ss.card}>
          <BudgetBar used={session.budget.used} total={session.budget.total} costUsd={session.budget.costUsd} />
        </View>

        {/* Quick stats */}
        <View style={ss.statsRow}>
          {[
            { label: 'Total', value: session.toolCalls.length, color: C.text },
            { label: 'Allowed', value: session.toolCalls.filter(t => t.decision === 'allow').length, color: C.green },
            { label: 'Ask', value: session.toolCalls.filter(t => t.decision === 'ask').length, color: C.amber },
            { label: 'Blocked', value: session.toolCalls.filter(t => t.decision === 'block').length, color: C.red },
          ].map(stat => (
            <View key={stat.label} style={ss.statCard}>
              <Text style={[ss.statVal, { color: stat.color }]}>{stat.value}</Text>
              <Text style={ss.statLbl}>{stat.label}</Text>
            </View>
          ))}
        </View>

        {/* Pending approval banner */}
        {session.pendingApprovals.length > 0 && (
          <TouchableOpacity style={ss.approvalBanner} onPress={() => setShowApproval(true)} activeOpacity={0.85}>
            <Text style={ss.approvalBannerText}>⚠ Approval needed: {approval?.tool}</Text>
            <Text style={ss.approvalBannerSub}>Tap to review →</Text>
          </TouchableOpacity>
        )}

        {/* Controls */}
        {!['ENDED', 'FAILED'].includes(session.state) && (
          <View style={ss.controls}>
            <TouchableOpacity
              style={ss.ctrlBtn}
              activeOpacity={0.8}
              onPress={() => setSession(s => ({ ...s, state: s.state === 'ACTIVE' ? 'PAUSED' : 'ACTIVE' }))}
            >
              <Text style={ss.ctrlBtnText}>{session.state === 'PAUSED' ? 'Resume' : 'Pause'}</Text>
            </TouchableOpacity>
            <TouchableOpacity style={ss.ctrlBtn} activeOpacity={0.8}>
              <Text style={ss.ctrlBtnText}>Take over</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[ss.ctrlBtn, ss.endBtn]}
              activeOpacity={0.8}
              onPress={() => {
                setSession(s => ({ ...s, state: 'ENDED', endedAt: new Date() }))
                setTimeout(() => navigation.navigate('Home'), 1500)
              }}
            >
              <Text style={[ss.ctrlBtnText, { color: C.red }]}>End</Text>
            </TouchableOpacity>
          </View>
        )}

        {/* Feed header */}
        <Text style={ss.feedTitle}>TOOL-CALL FEED</Text>

        {/* Feed */}
        <View style={ss.feedBox}>
          {session.toolCalls.length === 0 ? (
            <Text style={ss.feedEmpty}>Waiting for agent activity…</Text>
          ) : (
            session.toolCalls.map(ev => <FeedRow key={ev.id} event={ev} />)
          )}
        </View>

        {/* Ended card */}
        {session.state === 'ENDED' && (
          <View style={ss.endedCard}>
            <Text style={ss.endedTitle}>Workspace destroyed</Text>
            <Text style={ss.endedSub}>Container removed · credentials revoked · audit chain sealed</Text>
            <TouchableOpacity onPress={() => navigation.navigate('Audit')} style={ss.receiptBtn}>
              <Text style={ss.receiptBtnText}>View audit receipt →</Text>
            </TouchableOpacity>
          </View>
        )}

      </ScrollView>

      {/* Approval modal */}
      {approval && (
        <Modal visible={showApproval} transparent animationType="slide">
          <ApprovalSheet
            approval={approval}
            onApprove={() => handleApprove(approval.id)}
            onDeny={() => handleDeny(approval.id)}
          />
        </Modal>
      )}
    </SafeAreaView>
  )
}

function FeedRow({ event: ev }: { event: ToolCallEvent }) {
  const [expanded, setExpanded] = useState(false)
  const borderColor = { allow: '#34d39940', ask: '#fbbf2440', block: '#f8717140' }[ev.decision]

  return (
    <TouchableOpacity
      style={[ss.feedRow, { borderLeftColor: borderColor }]}
      onPress={() => setExpanded(e => !e)}
      activeOpacity={0.8}
    >
      <View style={ss.feedRowTop}>
        <Text style={ss.feedTool}>{ev.tool}</Text>
        <DecisionBadge decision={ev.decision} />
        <Text style={ss.feedTime}>{formatTime(ev.timestamp)}</Text>
      </View>
      <Text style={ss.feedArgs} numberOfLines={expanded ? 10 : 1}>
        {Object.entries(ev.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(' ')}
      </Text>
      {expanded && ev.reason && (
        <Text style={ss.feedReason}>{ev.reason}</Text>
      )}
    </TouchableOpacity>
  )
}

function ApprovalSheet({ approval, onApprove, onDeny }: {
  approval: ApprovalRequest
  onApprove: () => void
  onDeny: () => void
}) {
  const [secs, setSecs] = useState(
    Math.max(0, Math.floor((approval.expiresAt.getTime() - Date.now()) / 1000))
  )

  useEffect(() => {
    const id = setInterval(() => {
      setSecs(s => { if (s <= 1) { onDeny(); return 0 } return s - 1 })
    }, 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <View style={modal.overlay}>
      <View style={modal.sheet}>
        {/* Handle */}
        <View style={modal.handle} />

        <View style={modal.header}>
          <View>
            <Text style={modal.headerSub}>Approval Required</Text>
            <Text style={modal.headerTool}>{approval.tool}</Text>
          </View>
          <View style={modal.countdown}>
            <Text style={[modal.countdownNum, secs < 30 && { color: C.amber }]}>{secs}</Text>
            <Text style={modal.countdownLabel}>sec</Text>
          </View>
        </View>

        {/* Args */}
        <View style={modal.argsBox}>
          {Object.entries(approval.args).map(([k, v]) => (
            <View key={k} style={modal.argRow}>
              <Text style={modal.argKey}>{k}</Text>
              <Text style={modal.argVal}>{JSON.stringify(v)}</Text>
            </View>
          ))}
        </View>

        {/* Risk */}
        <View style={modal.riskRow}>
          <RiskBadge tier={approval.riskTier} />
          {!approval.reversible && (
            <Text style={modal.irreversible}>⚠ Cannot be undone</Text>
          )}
        </View>

        {/* Actions */}
        <View style={modal.actions}>
          <TouchableOpacity style={modal.denyBtn} onPress={onDeny} activeOpacity={0.8}>
            <Text style={modal.denyText}>Deny</Text>
          </TouchableOpacity>
          <TouchableOpacity style={modal.approveBtn} onPress={onApprove} activeOpacity={0.85}>
            <Text style={modal.approveText}>Approve</Text>
          </TouchableOpacity>
        </View>
      </View>
    </View>
  )
}

const ss = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.bg },
  topBar: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 16, paddingVertical: 10,
    borderBottomWidth: 1, borderBottomColor: C.border,
  },
  backBtn: { marginRight: 2 },
  backText: { color: C.textMuted, fontSize: 18 },
  stateName: { color: C.text, fontSize: 14, fontWeight: '700' },
  profileTag: { color: C.textMuted, fontSize: 11, fontFamily: 'monospace' },
  demoBtn: {
    backgroundColor: C.surface, borderWidth: 1, borderColor: C.border,
    paddingHorizontal: 10, paddingVertical: 5, borderRadius: 8,
  },
  demoBtnText: { color: C.accent, fontSize: 12, fontWeight: '600' },

  titleStrip: {
    paddingHorizontal: 16, paddingVertical: 10,
    borderBottomWidth: 1, borderBottomColor: C.borderSubtle,
  },
  title: { color: C.textMuted, fontSize: 13 },

  scroll: { flex: 1 },
  content: { padding: 16, gap: 12, paddingBottom: 60 },

  liveViewBox: {
    backgroundColor: C.surface, borderWidth: 1, borderColor: C.border,
    borderRadius: 14, overflow: 'hidden',
  },
  liveViewTitle: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 12, paddingVertical: 8,
    borderBottomWidth: 1, borderBottomColor: C.border, backgroundColor: C.surface2,
  },
  trafficLights: { flexDirection: 'row', gap: 4 },
  tl: { width: 10, height: 10, borderRadius: 5 },
  liveViewLabel: { color: C.textMuted, fontSize: 11, fontFamily: 'monospace', flex: 1, textAlign: 'center' },
  liveDot: {},
  liveDotText: { color: C.green, fontSize: 10, fontFamily: 'monospace' },
  liveViewCode: {
    padding: 12, color: C.green, fontFamily: 'monospace', fontSize: 12,
    lineHeight: 18, minHeight: 80,
  },

  card: {
    backgroundColor: C.surface, borderWidth: 1, borderColor: C.border,
    borderRadius: 14, padding: 14,
  },

  statsRow: { flexDirection: 'row', gap: 8 },
  statCard: {
    flex: 1, backgroundColor: C.surface, borderWidth: 1, borderColor: C.border,
    borderRadius: 12, padding: 10, alignItems: 'center',
  },
  statVal: { fontSize: 20, fontWeight: '700', fontFamily: 'monospace' },
  statLbl: { color: C.textMuted, fontSize: 9, marginTop: 2 },

  approvalBanner: {
    backgroundColor: '#451a0322', borderWidth: 1, borderColor: '#fbbf2440',
    borderRadius: 14, padding: 14,
  },
  approvalBannerText: { color: C.amber, fontSize: 14, fontWeight: '600' },
  approvalBannerSub: { color: '#fbbf2480', fontSize: 11, marginTop: 2 },

  controls: { flexDirection: 'row', gap: 8 },
  ctrlBtn: {
    flex: 1, backgroundColor: C.surface, borderWidth: 1, borderColor: C.border,
    borderRadius: 12, paddingVertical: 10, alignItems: 'center',
  },
  endBtn: { borderColor: '#f8717140', backgroundColor: '#450a0a22' },
  ctrlBtnText: { color: C.textMuted, fontSize: 13, fontWeight: '600' },

  feedTitle: {
    color: C.textMuted, fontSize: 10, fontWeight: '700',
    letterSpacing: 1.5, marginTop: 4,
  },
  feedBox: { gap: 4 },
  feedEmpty: { color: C.textSubtle, fontSize: 12, textAlign: 'center', paddingVertical: 24 },
  feedRow: {
    backgroundColor: C.surface, borderRadius: 10, padding: 10,
    borderLeftWidth: 2, gap: 4,
  },
  feedRowTop: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  feedTool: { color: C.text, fontSize: 12, fontFamily: 'monospace', flex: 1 },
  feedTime: { color: C.textSubtle, fontSize: 10, fontFamily: 'monospace' },
  feedArgs: { color: C.textMuted, fontSize: 10, fontFamily: 'monospace' },
  feedReason: { color: C.textMuted, fontSize: 10, fontStyle: 'italic' },

  endedCard: {
    backgroundColor: C.surface, borderWidth: 1, borderColor: C.border,
    borderRadius: 14, padding: 20, alignItems: 'center', gap: 6,
  },
  endedTitle: { color: C.text, fontSize: 16, fontWeight: '700' },
  endedSub: { color: C.textMuted, fontSize: 11, textAlign: 'center' },
  receiptBtn: { marginTop: 4 },
  receiptBtnText: { color: C.accent, fontSize: 13 },
})

const modal = StyleSheet.create({
  overlay: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.75)' },
  sheet: {
    backgroundColor: C.surface, borderTopLeftRadius: 24, borderTopRightRadius: 24,
    padding: 20, paddingBottom: 36, borderTopWidth: 1, borderColor: C.border,
  },
  handle: {
    width: 36, height: 4, backgroundColor: C.border, borderRadius: 2,
    alignSelf: 'center', marginBottom: 20,
  },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 },
  headerSub: { color: C.textMuted, fontSize: 10, fontWeight: '700', letterSpacing: 1.5, marginBottom: 4 },
  headerTool: { color: C.text, fontSize: 20, fontWeight: '700' },
  countdown: { alignItems: 'center' },
  countdownNum: { color: C.accent, fontSize: 24, fontWeight: '700', fontFamily: 'monospace' },
  countdownLabel: { color: C.textMuted, fontSize: 10 },

  argsBox: {
    backgroundColor: C.bg, borderWidth: 1, borderColor: C.border,
    borderRadius: 12, padding: 12, marginBottom: 12, gap: 6,
  },
  argRow: { flexDirection: 'row', gap: 8 },
  argKey: { color: C.textMuted, fontSize: 11, fontFamily: 'monospace', width: 80 },
  argVal: { color: C.text, fontSize: 11, fontFamily: 'monospace', flex: 1 },

  riskRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 20 },
  irreversible: { color: C.amber, fontSize: 12 },

  actions: { flexDirection: 'row', gap: 10 },
  denyBtn: {
    flex: 1, borderWidth: 1, borderColor: C.border, borderRadius: 14,
    paddingVertical: 14, alignItems: 'center',
  },
  denyText: { color: C.textMuted, fontSize: 15, fontWeight: '600' },
  approveBtn: { flex: 1, backgroundColor: C.indigo, borderRadius: 14, paddingVertical: 14, alignItems: 'center' },
  approveText: { color: '#fff', fontSize: 15, fontWeight: '700' },
})
