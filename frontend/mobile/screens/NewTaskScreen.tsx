import React, { useState } from 'react'
import { ScrollView, StyleSheet, Switch, Text, TextInput, TouchableOpacity, View } from 'react-native'
import { Feather } from '@expo/vector-icons'
import { SafeAreaView } from 'react-native-safe-area-context'
import { PROFILES, type ProfileId } from '../lib/mock'
import { draft } from '../lib/store'
import { BrandHeader, Btn, C, Card } from '../components/ui'

const ICONS: Record<ProfileId, keyof typeof Feather.glyphMap> = {
  coding: 'code',
  general: 'globe',
  daily: 'sun',
}

export function NewTaskScreen({ navigation }: { navigation: any }) {
  const [prompt, setPrompt] = useState(draft.prompt)
  const [profile, setProfile] = useState<ProfileId>(draft.profile)
  const [dry, setDry] = useState(draft.dryRun)

  function handleStart() {
    if (!prompt.trim()) return
    Object.assign(draft, { prompt: prompt.trim(), profile, dryRun: dry })
    navigation.navigate('PlanPreview')
  }

  return (
    <SafeAreaView style={st.safe} edges={['top', 'left', 'right']}>
      <BrandHeader onPressBrand={() => navigation.navigate('SessionsTab')} />
      <ScrollView contentContainerStyle={st.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
        <Text style={st.h1}>New task</Text>

        <View style={{ gap: 8 }}>
          <Text style={st.label}>What should the agent do?</Text>
          <TextInput
            value={prompt}
            onChangeText={setPrompt}
            multiline
            numberOfLines={4}
            textAlignVertical="top"
            placeholder="e.g. Fix flaky auth tests in api-server"
            placeholderTextColor={C.mutedFg}
            style={st.textarea}
          />
        </View>

        <View style={{ gap: 8 }}>
          <Text style={st.label}>Profile</Text>
          <View style={{ gap: 10 }}>
            {(Object.keys(PROFILES) as ProfileId[]).map((id) => {
              const p = PROFILES[id]
              const active = profile === id
              return (
                <TouchableOpacity
                  key={id}
                  activeOpacity={0.85}
                  onPress={() => setProfile(id)}
                  style={[st.profileCard, active && st.profileCardActive]}
                >
                  <View style={st.profileTop}>
                    <Feather name={ICONS[id]} size={18} color={C.primary} />
                  </View>
                  <Text style={st.profileName}>{p.name}</Text>
                  <Text style={st.profileDesc}>{p.desc}</Text>
                  <Text style={st.profileTools}>{p.tools.join(' · ')}</Text>
                </TouchableOpacity>
              )
            })}
          </View>
          <View style={st.infoRow}>
            <Feather name="info" size={14} color={C.mutedFg} style={{ marginTop: 2 }} />
            <Text style={st.infoText}>
              The profile limits which MCP tools exist in the session. Anything outside it is blocked by the gateway, regardless of what the agent asks for.
            </Text>
          </View>
        </View>

        <Card style={st.dryCard}>
          <View style={{ flex: 1 }}>
            <Text style={st.dryTitle}>Dry run</Text>
            <Text style={st.dryDesc}>Preview planned tool calls without executing writes.</Text>
          </View>
          <Switch
            value={dry}
            onValueChange={setDry}
            trackColor={{ false: C.muted, true: C.primary }}
            thumbColor={C.foreground}
          />
        </Card>

        <Btn style={{ minHeight: 48 }} disabled={!prompt.trim()} onPress={handleStart}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text style={{ color: C.primaryFg, fontSize: 15, fontWeight: '700' }}>Start task</Text>
            <Feather name="arrow-right" size={16} color={C.primaryFg} />
          </View>
        </Btn>
      </ScrollView>
    </SafeAreaView>
  )
}

const st = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.background },
  content: { padding: 16, paddingBottom: 32, gap: 18 },
  h1: { color: C.foreground, fontSize: 24, fontWeight: '700', letterSpacing: -0.4 },
  label: { color: C.foreground, fontSize: 14, fontWeight: '600' },
  textarea: {
    borderRadius: 12,
    borderWidth: 1,
    borderColor: C.border,
    backgroundColor: C.surface,
    color: C.foreground,
    padding: 12,
    minHeight: 104,
    fontSize: 15,
    lineHeight: 22,
  },
  profileCard: {
    borderRadius: 12,
    borderWidth: 1,
    borderColor: C.border,
    backgroundColor: C.card,
    padding: 14,
  },
  profileCardActive: {
    borderColor: C.primary,
    backgroundColor: C.accent,
  },
  profileTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  profileName: { color: C.foreground, fontSize: 15, fontWeight: '700', marginTop: 8 },
  profileDesc: { color: C.mutedFg, fontSize: 12, marginTop: 2 },
  profileTools: { color: C.mutedFg, fontSize: 11, fontFamily: C.mono, marginTop: 8 },
  infoRow: { flexDirection: 'row', gap: 8, marginTop: 4 },
  infoText: { flex: 1, color: C.mutedFg, fontSize: 12, lineHeight: 17 },
  dryCard: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  dryTitle: { color: C.foreground, fontSize: 15, fontWeight: '600' },
  dryDesc: { color: C.mutedFg, fontSize: 12, marginTop: 2 },
})
