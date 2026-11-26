import React, { useState, useRef } from 'react'
import {
  View, Text, StyleSheet, TextInput, TouchableOpacity,
  ScrollView, SafeAreaView, StatusBar, Animated,
  KeyboardAvoidingView, Platform,
} from 'react-native'
import { T, PrimaryBtn } from '../components/ui'

const PROFILES = [
  {
    id: 'coding',
    label: 'Coding',
    desc: 'Write, run, debug code',
    tools: ['terminal', 'files', 'desktop'],
    icon: '⬛',
  },
  {
    id: 'general',
    label: 'General',
    desc: 'Browse, research, write',
    tools: ['browser', 'files', 'desktop'],
    icon: '◎',
  },
  {
    id: 'daily_routine',
    label: 'Routine',
    desc: 'Calendar & email drafts',
    tools: ['calendar', 'gmail'],
    icon: '▦',
    badge: 'DEMO',
  },
]

const SUGGESTIONS = [
  'Create a Python script that fetches a page title and runs it',
  'Research recent papers on transformer safety and summarize',
  'Set up a TypeScript monorepo with ESLint and Prettier config',
  'Draft a reply to my email about the project deadline',
]

interface NewTaskScreenProps { navigation: any }

export function NewTaskScreen({ navigation }: NewTaskScreenProps) {
  const [task, setTask] = useState('')
  const [profile, setProfile] = useState('coding')
  const [dryRun, setDryRun] = useState(false)
  const [starting, setStarting] = useState(false)
  const inputRef = useRef<any>(null)
  const charCount = task.length

  async function handleStart() {
    if (!task.trim() || starting) return
    setStarting(true)
    await new Promise(r => setTimeout(r, 1000))
    navigation.navigate('Session', { sessionId: 'sess_abc123' })
    setStarting(false)
    setTask('')
  }

  return (
    <SafeAreaView style={s.safe}>
      <StatusBar barStyle="light-content" />
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={s.content}
          keyboardShouldPersistTaps="handled"
        >
          {/* Header */}
          <View style={s.header}>
            <View style={s.headerLeft}>
              <Text style={s.heading}>New task</Text>
              <Text style={s.sub}>Describe what the agent should do</Text>
            </View>
            <TouchableOpacity onPress={() => navigation.goBack()} style={s.cancelBtn}>
              <Text style={s.cancelText}>Cancel</Text>
            </TouchableOpacity>
          </View>

          {/* Task input — the hero element */}
          <TouchableOpacity
            activeOpacity={1}
            style={s.inputCard}
            onPress={() => inputRef.current?.focus()}
          >
            <TextInput
              ref={inputRef}
              value={task}
              onChangeText={setTask}
              placeholder="What should the agent do?"
              placeholderTextColor={T.textFaint}
              multiline
              style={s.input}
              textAlignVertical="top"
              autoFocus
            />
            {/* Character count + voice hint */}
            <View style={s.inputFooter}>
              <Text style={s.charCount}>{charCount > 0 ? charCount : ''}</Text>
              <Text style={s.voiceHint}>🎤  voice input  ·  stretch</Text>
            </View>
          </TouchableOpacity>

          {/* Suggestions */}
          {!task && (
            <View style={s.suggestSection}>
              <Text style={s.suggestLabel}>Try an example</Text>
              {SUGGESTIONS.map((sg, i) => (
                <TouchableOpacity
                  key={i}
                  style={s.suggestRow}
                  onPress={() => setTask(sg)}
                  activeOpacity={0.75}
                >
                  <Text style={s.suggestIcon}>›</Text>
                  <Text style={s.suggestText}>{sg}</Text>
                </TouchableOpacity>
              ))}
            </View>
          )}

          {/* Profile selector */}
          <View style={s.section}>
            <Text style={s.sectionLabel}>Profile</Text>
            <View style={s.profileGrid}>
              {PROFILES.map(p => {
                const active = profile === p.id
                return (
                  <TouchableOpacity
                    key={p.id}
                    style={[s.profileCard, active && s.profileCardActive]}
                    onPress={() => setProfile(p.id)}
                    activeOpacity={0.8}
                  >
                    {/* Selection indicator */}
                    <View style={[s.profileRadio, active && s.profileRadioActive]}>
                      {active && <View style={s.profileRadioDot} />}
                    </View>

                    <Text style={[s.profileIcon, { opacity: active ? 1 : 0.5 }]}>{p.icon}</Text>
                    <Text style={[s.profileName, active && s.profileNameActive]}>{p.label}</Text>
                    <Text style={s.profileDesc}>{p.desc}</Text>

                    {/* Tool chips */}
                    <View style={s.toolChips}>
                      {p.tools.map(t => (
                        <View key={t} style={[s.toolChip, active && s.toolChipActive]}>
                          <Text style={[s.toolChipText, active && s.toolChipTextActive]}>{t}</Text>
                        </View>
                      ))}
                    </View>

                    {p.badge && (
                      <View style={s.demoBadge}>
                        <Text style={s.demoBadgeText}>{p.badge}</Text>
                      </View>
                    )}
                  </TouchableOpacity>
                )
              })}
            </View>
          </View>

          {/* Options */}
          <View style={s.section}>
            <Text style={s.sectionLabel}>Options</Text>
            <View style={s.optionCard}>
              {/* Dry-run */}
              <TouchableOpacity
                style={s.optionRow}
                onPress={() => setDryRun(d => !d)}
                activeOpacity={0.8}
              >
                <View style={s.optionLeft}>
                  <Text style={s.optionTitle}>Dry-run</Text>
                  <Text style={s.optionDesc}>Reads run; writes are recorded not applied</Text>
                </View>
                <View style={[s.toggle, dryRun && s.toggleOn]}>
                  <View style={[s.toggleThumb, dryRun && s.toggleThumbOn]} />
                </View>
              </TouchableOpacity>

              <View style={s.optionDivider} />

              {/* Plan preview — always on for demo */}
              <View style={[s.optionRow, { opacity: 0.6 }]}>
                <View style={s.optionLeft}>
                  <Text style={s.optionTitle}>Plan preview  <Text style={s.stretchTag}>STRETCH</Text></Text>
                  <Text style={s.optionDesc}>Review steps before the agent runs</Text>
                </View>
                <View style={[s.toggle, s.toggleOn]}>
                  <View style={[s.toggleThumb, s.toggleThumbOn]} />
                </View>
              </View>
            </View>
          </View>

          {/* Gateway note */}
          <View style={s.gatewayNote}>
            <Text style={s.gatewayIcon}>◈</Text>
            <Text style={s.gatewayText}>
              Every tool call passes through the MCP gateway.{'\n'}
              Sensitive actions require your approval.
            </Text>
          </View>

          {/* Start */}
          <View style={s.startWrap}>
            <PrimaryBtn
              label={starting ? 'Starting session…' : 'Start session'}
              onPress={handleStart}
              loading={starting}
              disabled={!task.trim()}
            />
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  )
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: T.bg },
  content: { paddingBottom: 60 },

  header: {
    flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between',
    paddingHorizontal: 24, paddingTop: 20, paddingBottom: 16,
  },
  headerLeft: { gap: 3 },
  heading: { color: T.text, fontSize: 26, fontWeight: '800', letterSpacing: -0.8 },
  sub: { color: T.textDim, fontSize: 13 },
  cancelBtn: { paddingTop: 4 },
  cancelText: { color: T.textSub, fontSize: 15 },

  // Input card — the centrepiece
  inputCard: {
    marginHorizontal: 16, marginBottom: 20,
    backgroundColor: T.surface, borderRadius: 20,
    borderWidth: 1, borderColor: T.borderMid,
    padding: 16, minHeight: 140,
  },
  input: {
    color: T.text, fontSize: 16, lineHeight: 24,
    flex: 1, minHeight: 90,
  },
  inputFooter: {
    flexDirection: 'row', justifyContent: 'space-between',
    alignItems: 'center', marginTop: 10,
  },
  charCount: { color: T.textFaint, fontSize: 11, fontFamily: T.mono },
  voiceHint: { color: T.textFaint, fontSize: 10 },

  // Suggestions
  suggestSection: { paddingHorizontal: 20, marginBottom: 24 },
  suggestLabel: {
    color: T.textDim, fontSize: 11, fontWeight: '700',
    letterSpacing: 1.5, textTransform: 'uppercase', marginBottom: 10,
  },
  suggestRow: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 10,
    paddingVertical: 9,
    borderBottomWidth: 1, borderBottomColor: T.border + '44',
  },
  suggestIcon: { color: T.textFaint, fontSize: 16, marginTop: 1 },
  suggestText: { color: T.textSub, fontSize: 13, flex: 1, lineHeight: 19 },

  section: { paddingHorizontal: 16, marginBottom: 20 },
  sectionLabel: {
    color: T.textDim, fontSize: 11, fontWeight: '700',
    letterSpacing: 1.5, textTransform: 'uppercase', marginBottom: 12,
  },

  // Profile grid
  profileGrid: { flexDirection: 'row', gap: 10 },
  profileCard: {
    flex: 1, backgroundColor: T.surface, borderRadius: 18,
    borderWidth: 1, borderColor: T.border, padding: 14, gap: 4,
    position: 'relative',
  },
  profileCardActive: {
    borderColor: T.indigo + '60', backgroundColor: T.indigoDim,
  },
  profileRadio: {
    width: 16, height: 16, borderRadius: 8, borderWidth: 1.5,
    borderColor: T.border, alignItems: 'center', justifyContent: 'center',
    marginBottom: 6,
  },
  profileRadioActive: { borderColor: T.indigo },
  profileRadioDot: {
    width: 8, height: 8, borderRadius: 4, backgroundColor: T.indigo,
  },
  profileIcon: { fontSize: 18, marginBottom: 2 },
  profileName: { color: T.textSub, fontSize: 13, fontWeight: '700' },
  profileNameActive: { color: T.text },
  profileDesc: { color: T.textDim, fontSize: 10, lineHeight: 14, marginBottom: 8 },
  toolChips: { flexDirection: 'row', flexWrap: 'wrap', gap: 4 },
  toolChip: {
    backgroundColor: T.elevated, borderRadius: 6,
    paddingHorizontal: 6, paddingVertical: 2,
  },
  toolChipActive: { backgroundColor: T.indigo + '20' },
  toolChipText: { color: T.textDim, fontSize: 9, fontFamily: T.mono },
  toolChipTextActive: { color: T.indigo },
  demoBadge: {
    position: 'absolute', top: 8, right: 8,
    backgroundColor: T.violetDim, borderRadius: 6,
    paddingHorizontal: 5, paddingVertical: 2,
    borderWidth: 1, borderColor: T.violet + '40',
  },
  demoBadgeText: { color: T.violet, fontSize: 8, fontWeight: '800' },

  // Options card
  optionCard: {
    backgroundColor: T.surface, borderRadius: 18,
    borderWidth: 1, borderColor: T.border,
  },
  optionRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    padding: 16, gap: 12,
  },
  optionLeft: { flex: 1 },
  optionTitle: { color: T.text, fontSize: 14, fontWeight: '500', marginBottom: 2 },
  optionDesc: { color: T.textDim, fontSize: 11, lineHeight: 16 },
  optionDivider: { height: 1, backgroundColor: T.border, marginHorizontal: 16 },
  stretchTag: {
    color: T.violet, fontSize: 9, fontWeight: '800', letterSpacing: 0.5,
  },
  toggle: {
    width: 42, height: 24, borderRadius: 12,
    backgroundColor: T.border, padding: 2,
    justifyContent: 'center',
  },
  toggleOn: { backgroundColor: T.indigo },
  toggleThumb: {
    width: 20, height: 20, borderRadius: 10, backgroundColor: T.textDim,
    transform: [{ translateX: 0 }],
  },
  toggleThumbOn: { backgroundColor: '#fff', transform: [{ translateX: 18 }] },

  gatewayNote: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 10,
    marginHorizontal: 20, marginBottom: 24,
    backgroundColor: T.elevated, borderRadius: 14,
    borderWidth: 1, borderColor: T.border,
    padding: 14,
  },
  gatewayIcon: { color: T.indigo, fontSize: 16, marginTop: 1 },
  gatewayText: { color: T.textDim, fontSize: 12, lineHeight: 18, flex: 1 },

  startWrap: { paddingHorizontal: 16 },
})
