import React from 'react'
import { ScrollView, StyleSheet, Text, View } from 'react-native'
import { Feather } from '@expo/vector-icons'
import { SafeAreaView } from 'react-native-safe-area-context'
import { PLAN_STEPS, PROFILES } from '../lib/mock'
import { draft, useStore } from '../lib/store'
import { useToast } from '../lib/toast'
import { BrandHeader, Btn, C, Card, RiskBadge } from '../components/ui'

export function PlanPreviewScreen({ navigation }: { navigation: any }) {
  const { createSession } = useStore()
  const toast = useToast()
  const p = PROFILES[draft.profile]
  const steps = PLAN_STEPS.filter((s) =>
    draft.profile !== 'coding' ? !['shell.exec', 'git.commit'].includes(s.tool) : true,
  )

  function handleApprove() {
    const id = createSession(draft.prompt, draft.profile)
    navigation.replace('Session', { id })
  }

  return (
    <SafeAreaView style={st.safe} edges={['top', 'left', 'right']}>
      <BrandHeader onPressBrand={() => navigation.navigate('MainTabs')} />
      <ScrollView contentContainerStyle={st.content} showsVerticalScrollIndicator={false}>
        <View>
          <Text style={st.h1}>Plan preview</Text>
          <Text style={st.prompt}>{draft.prompt}</Text>
          <Text style={st.meta}>
            {p.name} profile{draft.dryRun ? ' · dry run' : ''}
          </Text>
        </View>

        <View style={{ gap: 8 }}>
          {steps.map((s, i) => (
            <View key={i} style={st.stepCard}>
              <View style={st.stepNum}>
                <Text style={st.stepNumText}>{i + 1}</Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={st.stepTool}>{s.tool}</Text>
                <Text style={st.stepDesc}>{s.desc}</Text>
                <View style={st.stepBadges}>
                  <RiskBadge r={s.risk} />
                  {s.reversible ? (
                    <View style={st.badgeInline}>
                      <Feather name="rotate-ccw" size={11} color={C.mutedFg} />
                      <Text style={st.revText}>reversible</Text>
                    </View>
                  ) : (
                    <View style={st.badgeInline}>
                      <Feather name="alert-triangle" size={11} color={C.block} />
                      <Text style={st.irrevText}>irreversible · will ask</Text>
                    </View>
                  )}
                </View>
              </View>
            </View>
          ))}
        </View>

        <Card style={st.budgetRow}>
          <Text style={st.budgetLabel}>Estimated budget</Text>
          <Text style={st.budgetVal}>~$0.60 of $2.00</Text>
        </Card>

        <View style={st.actions}>
          <Btn variant="secondary" style={{ flex: 1 }} onPress={() => navigation.goBack()}>
            <View style={st.btnInner}>
              <Feather name="edit-2" size={14} color={C.secondaryFg} />
              <Text style={st.btnSecText}>Edit</Text>
            </View>
          </Btn>
          <Btn
            variant="secondary"
            style={{ flex: 1 }}
            onPress={() =>
              toast('Dry run complete', {
                description: `${steps.length} steps evaluated, 0 executed.`,
              })
            }
          >
            <View style={st.btnInner}>
              <Feather name="activity" size={14} color={C.secondaryFg} />
              <Text style={st.btnSecText}>Dry run</Text>
            </View>
          </Btn>
          <Btn style={{ flex: 1 }} onPress={handleApprove}>
            <View style={st.btnInner}>
              <Feather name="check" size={14} color={C.primaryFg} />
              <Text style={st.btnPriText}>Approve</Text>
            </View>
          </Btn>
        </View>

        <Text style={st.footnote}>
          Off-plan calls escalate to user approval automatically.
        </Text>
      </ScrollView>
    </SafeAreaView>
  )
}

const st = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.background },
  content: { padding: 16, paddingBottom: 32, gap: 16 },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  h1: { color: C.foreground, fontSize: 24, fontWeight: '700', letterSpacing: -0.4 },
  prompt: { color: C.mutedFg, fontSize: 14, marginTop: 6 },
  meta: { color: C.mutedFg, fontSize: 12, fontFamily: C.mono, marginTop: 3 },

  stepCard: {
    flexDirection: 'row',
    gap: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: C.border,
    backgroundColor: C.card,
    padding: 12,
  },
  stepNum: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: C.muted,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stepNumText: { color: C.foreground, fontSize: 12, fontFamily: C.mono, fontWeight: '600' },
  stepTool: { color: C.foreground, fontSize: 14, fontFamily: C.mono, fontWeight: '700' },
  stepDesc: { color: C.mutedFg, fontSize: 13, marginTop: 2 },
  stepBadges: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, marginTop: 6, alignItems: 'center' },
  badgeInline: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  revText: { color: C.mutedFg, fontSize: 11 },
  irrevText: { color: C.block, fontSize: 11, fontWeight: '700' },

  budgetRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  budgetLabel: { color: C.mutedFg, fontSize: 14 },
  budgetVal: { color: C.foreground, fontSize: 14, fontFamily: C.mono, fontWeight: '700' },

  actions: { flexDirection: 'row', gap: 8 },
  btnInner: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  btnSecText: { color: C.secondaryFg, fontSize: 13, fontWeight: '700' },
  btnPriText: { color: C.primaryFg, fontSize: 13, fontWeight: '700' },
  footnote: { color: C.mutedFg, fontSize: 12, textAlign: 'center', marginTop: 4 },
})
