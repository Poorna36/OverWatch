import React, { useState } from 'react'
import { ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native'
import { Feather } from '@expo/vector-icons'
import { SafeAreaView } from 'react-native-safe-area-context'
import { PROFILES } from '../lib/mock'
import { useStore, type Tier } from '../lib/store'
import { useToast } from '../lib/toast'
import { ApprovalSheet } from '../components/ApprovalSheet'
import { BrandHeader, Btn, Budget, C, CallRow, Card, StateBadge } from '../components/ui'

const TIERS: { id: Tier; label: string; icon: keyof typeof Feather.glyphMap }[] = [
  { id: 'video', label: 'Video', icon: 'monitor' },
  { id: 'screenshot', label: 'Screens', icon: 'image' },
  { id: 'text', label: 'Text', icon: 'type' },
]

export function SessionScreen({ navigation, route }: { navigation: any; route: any }) {
  const id: string = route.params?.id
  const store = useStore()
  const toast = useToast()
  const s = store.sessions.find((x) => x.id === id)
  const [review, setReview] = useState<number | null>(null)

  if (!s) {
    return (
      <SafeAreaView style={st.safe} edges={['top', 'left', 'right']}>
        <BrandHeader onPressBrand={() => navigation.navigate('MainTabs')} />
        <View style={{ padding: 16 }}>
          <Card style={{ alignItems: 'center', gap: 10 }}>
            <Text style={{ color: C.foreground, fontSize: 15 }}>Session not found.</Text>
            <TouchableOpacity onPress={() => navigation.goBack()}>
              <Text style={{ color: C.primary, textDecorationLine: 'underline' }}>Back to sessions</Text>
            </TouchableOpacity>
          </Card>
        </View>
      </SafeAreaView>
    )
  }

  const pending = s.calls.find((c) => c.status === 'pending')
  const reviewing = s.calls.find((c) => c.seq === review)
  const ended = s.state === 'ENDED'
  const paused = s.state === 'PAUSED'
  const feed = [...s.calls].reverse()

  const Feed = (
    <View style={{ gap: 10 }}>
      <View style={st.feedHeader}>
        <Text style={st.feedTitle}>TOOL CALLS · {s.calls.length}</Text>
        {!ended ? (
          <TouchableOpacity
            disabled={paused}
            onPress={() => store.appendDemo(s.id)}
            style={[st.simBtn, paused && { opacity: 0.4 }]}
          >
            <Feather name="plus" size={14} color={C.mutedFg} />
            <Text style={st.simBtnText}>Next step</Text>
          </TouchableOpacity>
        ) : null}
      </View>

      {feed.length === 0 ? (
        <Card style={{ alignItems: 'center', paddingVertical: 20 }}>
          <Text style={{ color: C.mutedFg, fontSize: 13, textAlign: 'center' }}>
            Waiting for the agent's first tool call…
          </Text>
        </Card>
      ) : (
        <View style={{ gap: 8 }}>
          {feed.map((c) => (
            <CallRow key={c.seq} c={c} onReview={() => setReview(c.seq)} />
          ))}
        </View>
      )}
    </View>
  )

  return (
    <SafeAreaView style={st.safe} edges={['top', 'left', 'right', 'bottom']}>
      <BrandHeader onPressBrand={() => navigation.navigate('MainTabs')} />

      <ScrollView contentContainerStyle={st.content} showsVerticalScrollIndicator={false}>
        <TouchableOpacity style={st.backRow} onPress={() => navigation.goBack()}>
          <Feather name="arrow-left" size={16} color={C.mutedFg} />
          <Text style={st.backText}>Sessions</Text>
        </TouchableOpacity>

        <View>
          <StateBadge s={s.state} big />
          <Text style={st.h1}>{s.title}</Text>
          <Text style={st.meta}>
            {PROFILES[s.profile].name} · {s.id} · sandbox: hardened container
          </Text>
        </View>

        {pending && !ended ? (
          <TouchableOpacity
            activeOpacity={0.85}
            onPress={() => setReview(pending.seq)}
            style={st.pendingBanner}
          >
            <View style={st.pingDot} />
            <Text style={st.pendingText} numberOfLines={1}>
              <Text style={{ fontWeight: '700', color: C.ask }}>Approval needed: </Text>
              <Text style={{ fontFamily: C.mono }}>{pending.tool}</Text>
            </Text>
            <Text style={st.pendingCta}>Review</Text>
          </TouchableOpacity>
        ) : null}

        <View style={st.tierGroup}>
          {TIERS.map((t) => {
            const active = store.tier === t.id
            return (
              <TouchableOpacity
                key={t.id}
                onPress={() => store.setTier(t.id)}
                style={[st.tierBtn, active && st.tierBtnActive]}
              >
                <Feather name={t.icon} size={15} color={active ? C.foreground : C.mutedFg} />
                <Text style={[st.tierText, active && { color: C.foreground }]}>{t.label}</Text>
              </TouchableOpacity>
            )
          })}
        </View>

        {store.tier === 'text' ? (
          Feed
        ) : (
          <>
            <View style={st.frameWrap}>
              <View style={st.frameInner}>
                <Text style={st.framePrompt}>agent@sandbox:/workspace/api-server$</Text>
                {s.calls.slice(-4).map((c) => (
                  <Text key={c.seq} style={st.frameLine} numberOfLines={1}>
                    &gt; {c.tool} {Object.values(c.args)[0]}
                  </Text>
                ))}
                {!paused && !ended ? <Text style={st.cursor}>▍</Text> : null}
              </View>

              <View style={st.frameTopBadge}>
                {store.tier === 'video' ? (
                  <View style={st.frameBadgeRow}>
                    <View style={[st.liveDot]} />
                    <Text style={st.frameBadgeText}>LIVE · 12fps</Text>
                  </View>
                ) : (
                  <View style={st.frameBadgeRow}>
                    <Feather name="image" size={11} color={C.foreground} />
                    <Text style={st.frameBadgeText}>every 5s</Text>
                  </View>
                )}
              </View>

              {paused || ended ? (
                <View style={st.frameOverlay}>
                  <Text style={st.frameOverlayText}>{s.state}</Text>
                </View>
              ) : null}
            </View>

            <Card style={{ paddingVertical: 12 }}>
              <Budget used={s.budget.used} limit={s.budget.limit} />
            </Card>
          </>
        )}

        {store.tier === 'text' ? (
          <Card style={{ paddingVertical: 12 }}>
            <Budget used={s.budget.used} limit={s.budget.limit} />
          </Card>
        ) : (
          Feed
        )}

        <View style={st.connRow}>
          <Feather name="wifi" size={12} color={C.mutedFg} />
          <Text style={st.connText}>Stream tier: {store.tier}</Text>
        </View>
      </ScrollView>

      {/* Sticky controls bar */}
      <View style={st.controlsBar}>
        <Btn
          variant="secondary"
          style={{ flex: 1 }}
          disabled={ended}
          onPress={() => store.setState(s.id, paused ? 'ACTIVE' : 'PAUSED')}
        >
          <View style={st.btnInner}>
            <Feather name={paused ? 'play' : 'pause'} size={15} color={C.secondaryFg} />
            <Text style={st.btnSecText}>{paused ? 'Resume' : 'Pause'}</Text>
          </View>
        </Btn>
        <Btn
          variant="secondary"
          style={{ flex: 1 }}
          disabled={ended}
          onPress={() =>
            toast('Take over requested', {
              description: 'Handing desktop control to your device.',
            })
          }
        >
          <View style={st.btnInner}>
            <Feather name="mouse-pointer" size={15} color={C.secondaryFg} />
            <Text style={st.btnSecText}>Take over</Text>
          </View>
        </Btn>
        <Btn
          variant="danger"
          style={{ flex: 1 }}
          disabled={ended}
          onPress={() => {
            store.setState(s.id, 'ENDED')
            toast('Session ended')
          }}
        >
          <View style={st.btnInner}>
            <Feather name="power" size={15} color={C.block} />
            <Text style={[st.btnSecText, { color: C.block }]}>End</Text>
          </View>
        </Btn>
      </View>

      {reviewing && reviewing.status === 'pending' ? (
        <ApprovalSheet
          call={reviewing}
          onClose={() => setReview(null)}
          onDecide={(ok, expired) => {
            store.decide(s.id, reviewing.seq, ok, expired)
            setReview(null)
            toast(
              ok
                ? 'Approved — files.upload executed'
                : expired
                  ? 'Approval expired — denied'
                  : 'Denied — call not executed',
            )
          }}
        />
      ) : null}
    </SafeAreaView>
  )
}

const st = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.background },
  content: { padding: 16, paddingBottom: 24, gap: 14 },
  backRow: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 4, alignSelf: 'flex-start' },
  backText: { color: C.mutedFg, fontSize: 14 },
  h1: { color: C.foreground, fontSize: 20, fontWeight: '700', marginTop: 8, letterSpacing: -0.3 },
  meta: { color: C.mutedFg, fontSize: 12, fontFamily: C.mono, marginTop: 3 },

  pendingBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: C.ask + '80',
    backgroundColor: C.ask + '1a',
    padding: 12,
  },
  pingDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: C.ask },
  pendingText: { flex: 1, color: C.foreground, fontSize: 13 },
  pendingCta: { color: C.ask, fontSize: 13, fontWeight: '700' },

  tierGroup: {
    flexDirection: 'row',
    gap: 4,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: C.border,
    backgroundColor: C.surface,
    padding: 4,
  },
  tierBtn: {
    flex: 1,
    minHeight: 38,
    borderRadius: 7,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
  },
  tierBtnActive: { backgroundColor: C.accent },
  tierText: { color: C.mutedFg, fontSize: 12, fontWeight: '600' },

  frameWrap: {
    aspectRatio: 16 / 9,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: C.border,
    backgroundColor: C.surface,
    overflow: 'hidden',
    position: 'relative',
  },
  frameInner: {
    position: 'absolute',
    top: 10,
    left: 10,
    right: 10,
    bottom: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: C.border,
    backgroundColor: C.card + '99',
    padding: 12,
    gap: 3,
  },
  framePrompt: { color: C.foreground, fontFamily: C.mono, fontSize: 11 },
  frameLine: { color: C.mutedFg, fontFamily: C.mono, fontSize: 11 },
  cursor: { color: C.foreground, fontFamily: C.mono, fontSize: 12 },
  frameTopBadge: {
    position: 'absolute',
    left: 8,
    top: 8,
    backgroundColor: C.background + 'cc',
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  frameBadgeRow: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  liveDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: C.block },
  frameBadgeText: { color: C.foreground, fontFamily: C.mono, fontSize: 10 },
  frameBottomTag: { position: 'absolute', right: 8, bottom: 8 },
  frameOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: C.background + 'aa',
    alignItems: 'center',
    justifyContent: 'center',
  },
  frameOverlayText: { color: C.foreground, fontFamily: C.mono, fontSize: 14, fontWeight: '700' },

  feedHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  feedTitle: { color: C.mutedFg, fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  simBtn: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingVertical: 4, paddingHorizontal: 6 },
  simBtnText: { color: C.mutedFg, fontSize: 12, fontWeight: '600' },

  connRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 4 },
  connText: { color: C.mutedFg, fontSize: 12 },

  controlsBar: {
    flexDirection: 'row',
    gap: 8,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: C.border,
    backgroundColor: C.popover,
  },
  btnInner: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  btnSecText: { color: C.secondaryFg, fontSize: 13, fontWeight: '700' },
})
