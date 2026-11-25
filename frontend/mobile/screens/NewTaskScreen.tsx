import React, { useState } from 'react'
import {
  View, Text, StyleSheet, TextInput, TouchableOpacity,
  ScrollView, SafeAreaView, StatusBar, Switch,
} from 'react-native'
import { C } from '../components/ui'

const PROFILES = [
  { name: 'coding', tools: ['terminal', 'files', 'desktop'] },
  { name: 'general', tools: ['files', 'browser', 'desktop'] },
  { name: 'daily_routine', tools: ['calendar', 'gmail'] },
]

const EXAMPLES = [
  'Create a Python script that fetches a page title',
  'Research papers on transformer architectures',
  'Set up a TypeScript monorepo with ESLint',
]

interface NewTaskScreenProps { navigation: any }

export function NewTaskScreen({ navigation }: NewTaskScreenProps) {
  const [task, setTask] = useState('')
  const [profile, setProfile] = useState('coding')
  const [dryRun, setDryRun] = useState(false)
  const [starting, setStarting] = useState(false)

  async function handleStart() {
    if (!task.trim()) return
    setStarting(true)
    await new Promise(r => setTimeout(r, 1200))
    navigation.navigate('Session', { sessionId: 'sess_abc123' })
    setStarting(false)
  }

  return (
    <SafeAreaView style={s.safe}>
      <StatusBar barStyle="light-content" backgroundColor={C.bg} />
      <ScrollView style={s.scroll} contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">

        {/* Back row */}
        <TouchableOpacity style={s.backRow} onPress={() => navigation.goBack()} activeOpacity={0.7}>
          <Text style={s.backText}>← Back</Text>
        </TouchableOpacity>

        <Text style={s.heading}>New task</Text>
        <Text style={s.sub}>Describe what the agent should do</Text>

        {/* Input */}
        <View style={s.section}>
          <Text style={s.label}>TASK DESCRIPTION</Text>
          <TextInput
            value={task}
            onChangeText={setTask}
            placeholder="What should the agent do?"
            placeholderTextColor={C.textSubtle}
            multiline
            numberOfLines={4}
            style={s.input}
            textAlignVertical="top"
          />
        </View>

        {/* Examples */}
        <View style={s.section}>
          <Text style={s.label}>EXAMPLES</Text>
          <View style={s.pills}>
            {EXAMPLES.map(e => (
              <TouchableOpacity key={e} style={s.pill} onPress={() => setTask(e)} activeOpacity={0.75}>
                <Text style={s.pillText} numberOfLines={1}>{e}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* Profile */}
        <View style={s.section}>
          <Text style={s.label}>PROFILE</Text>
          <View style={s.profiles}>
            {PROFILES.map(p => (
              <TouchableOpacity
                key={p.name}
                style={[s.profileCard, profile === p.name && s.profileCardActive]}
                onPress={() => setProfile(p.name)}
                activeOpacity={0.8}
              >
                <View style={s.profileHeader}>
                  <Text style={[s.profileName, profile === p.name && s.profileNameActive]}>
                    {p.name}
                  </Text>
                  {profile === p.name && <View style={s.profileDot} />}
                </View>
                <View style={s.toolPills}>
                  {p.tools.map(t => (
                    <View key={t} style={s.toolPill}>
                      <Text style={s.toolPillText}>{t}</Text>
                    </View>
                  ))}
                </View>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* Dry run toggle */}
        <View style={s.optionRow}>
          <View>
            <Text style={s.optionTitle}>Dry-run mode</Text>
            <Text style={s.optionSub}>Writes recorded but not applied</Text>
          </View>
          <Switch
            value={dryRun}
            onValueChange={setDryRun}
            trackColor={{ false: C.border, true: C.indigo }}
            thumbColor="#fff"
          />
        </View>

        {/* Start */}
        <TouchableOpacity
          style={[s.startBtn, (!task.trim() || starting) && s.startBtnDisabled]}
          onPress={handleStart}
          disabled={!task.trim() || starting}
          activeOpacity={0.85}
        >
          <Text style={s.startBtnText}>
            {starting ? 'Starting session…' : 'Start session'}
          </Text>
        </TouchableOpacity>

        <Text style={s.fine}>
          A sandboxed container will be created.{'\n'}Every tool call passes through the MCP gateway.
        </Text>
      </ScrollView>
    </SafeAreaView>
  )
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.bg },
  scroll: { flex: 1 },
  content: { padding: 20, paddingBottom: 60 },

  backRow: { marginBottom: 16 },
  backText: { color: C.textMuted, fontSize: 14 },
  heading: { color: C.text, fontSize: 26, fontWeight: '700', letterSpacing: -0.5 },
  sub: { color: C.textMuted, fontSize: 14, marginTop: 4, marginBottom: 24 },

  section: { marginBottom: 20 },
  label: { color: C.textMuted, fontSize: 10, fontWeight: '700', letterSpacing: 1.5, marginBottom: 8 },

  input: {
    backgroundColor: C.surface, borderWidth: 1, borderColor: C.border,
    borderRadius: 14, padding: 14, color: C.text, fontSize: 14,
    minHeight: 100, lineHeight: 20,
  },

  pills: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  pill: {
    backgroundColor: C.surface, borderWidth: 1, borderColor: C.border,
    borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6, maxWidth: '100%',
  },
  pillText: { color: C.textMuted, fontSize: 11 },

  profiles: { gap: 10 },
  profileCard: {
    backgroundColor: C.surface, borderWidth: 1, borderColor: C.border,
    borderRadius: 14, padding: 14,
  },
  profileCardActive: { borderColor: '#818cf860', backgroundColor: '#312e8120' },
  profileHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 },
  profileName: { color: C.textMuted, fontSize: 14, fontWeight: '600' },
  profileNameActive: { color: C.accent },
  profileDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: C.accent },
  toolPills: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  toolPill: { backgroundColor: C.surface2, borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3 },
  toolPillText: { color: C.textMuted, fontSize: 10, fontFamily: 'monospace' },

  optionRow: {
    backgroundColor: C.surface, borderWidth: 1, borderColor: C.border,
    borderRadius: 14, padding: 16, flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', marginBottom: 24,
  },
  optionTitle: { color: C.text, fontSize: 14, fontWeight: '500' },
  optionSub: { color: C.textMuted, fontSize: 11, marginTop: 2 },

  startBtn: {
    backgroundColor: C.indigo, borderRadius: 14, padding: 16,
    alignItems: 'center', marginBottom: 16,
  },
  startBtnDisabled: { opacity: 0.4 },
  startBtnText: { color: '#fff', fontSize: 16, fontWeight: '700' },

  fine: { color: C.textSubtle, fontSize: 11, textAlign: 'center', lineHeight: 16 },
})
