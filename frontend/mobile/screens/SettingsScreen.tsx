import React, { useState } from 'react'
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  StatusBar, Switch,
} from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import {
  type EnvConfig, VM_IMAGES, VM_DEPS_OPTIONS, VM_INCLUDES_OPTIONS, VM_MCP_OPTIONS,
} from '../lib/mock-data'
import { T, PulseDot, ProfileAvatar } from '../components/ui'

interface SettingsScreenProps {
  env: EnvConfig
  onChangeEnv: (env: EnvConfig) => void
  userEmail: string
  onLogout: () => void
}

export function SettingsScreen({ env, onChangeEnv, userEmail, onLogout }: SettingsScreenProps) {
  const [googleConnected, setGoogleConnected] = useState(true)
  const [approvalNotifs, setApprovalNotifs] = useState(true)
  const [vmBootState, setVmBootState] = useState<'idle' | 'booting' | 'running'>('idle')
  const [vmBootStep, setVmBootStep] = useState('')

  async function handleStartVm() {
    if (vmBootState === 'booting') return
    setVmBootState('booting')
    setVmBootStep(`Pulling ${env.vmImage}…`)
    await new Promise(r => setTimeout(r, 700))
    setVmBootStep(`Mounting ${env.vmMcpAgents.length} MCP agents…`)
    await new Promise(r => setTimeout(r, 700))
    setVmBootState('running')
    setVmBootStep(`VM running · ${env.vmDeps.length} deps ready`)
  }

  function toggleItem(list: string[], item: string, key: 'vmDeps' | 'vmIncludes' | 'vmMcpAgents') {
    const next = list.includes(item) ? list.filter(x => x !== item) : [...list, item]
    onChangeEnv({ ...env, [key]: next })
  }

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <StatusBar barStyle="light-content" />
      <ScrollView contentContainerStyle={s.content} showsVerticalScrollIndicator={false}>
        <View style={s.header}>
          <Text style={s.heading}>Profile & Settings</Text>
          <Text style={s.sub}>Account, workspace & VM config</Text>
        </View>

        {/* Profile Card with Avatar */}
        <View style={s.card}>
          <View style={s.rowBetween}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, flex: 1 }}>
              <ProfileAvatar size={52} online />
              <View style={{ flex: 1 }}>
                <Text style={[s.rowTitle, { fontSize: 16 }]}>Poorn</Text>
                <Text style={s.rowSub} numberOfLines={1}>{userEmail}</Text>
                <Text style={{ color: T.indigo, fontSize: 11, fontWeight: '600', marginTop: 3 }}>
                  {env.mode === 'desktop' ? 'Remote Desktop Connected' : 'VM Sandbox Active'}
                </Text>
              </View>
            </View>
            <TouchableOpacity style={s.logoutBtn} onPress={onLogout} activeOpacity={0.8}>
              <Text style={s.logoutText}>Sign out</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Environment Mode */}
        <Text style={s.sectionLabel}>EXECUTION ENVIRONMENT</Text>
        <View style={s.card}>
          <View style={s.modeRow}>
            <TouchableOpacity
              style={[s.modeBtn, env.mode === 'desktop' && s.modeBtnActive]}
              onPress={() => onChangeEnv({ ...env, mode: 'desktop' })}
            >
              <PulseDot color={env.mode === 'desktop' ? T.green : T.textDim} size={6} />
              <Text style={[s.modeText, env.mode === 'desktop' && s.modeTextActive]}>
                Remote Desktop
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[s.modeBtn, env.mode === 'vm' && s.modeBtnActive]}
              onPress={() => onChangeEnv({ ...env, mode: 'vm' })}
            >
              <Text style={[s.modeText, env.mode === 'vm' && s.modeTextActive]}>
                Isolated VM
              </Text>
            </TouchableOpacity>
          </View>

          {env.mode === 'desktop' ? (
            <Text style={s.envNote}>
              Active bridge: <Text style={{ color: T.text }}>{env.desktopHost}</Text>
            </Text>
          ) : (
            <View style={{ gap: 10, marginTop: 6 }}>
              <Text style={s.subLabel}>VM IMAGE</Text>
              <View style={s.chipWrap}>
                {VM_IMAGES.map(img => (
                  <TouchableOpacity
                    key={img.id}
                    style={[s.chip, env.vmImage === img.id && s.chipActive]}
                    onPress={() => onChangeEnv({ ...env, vmImage: img.id })}
                  >
                    <Text style={[s.chipText, env.vmImage === img.id && s.chipTextActive]}>
                      {img.label}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>

              <Text style={s.subLabel}>DEPENDENCIES</Text>
              <View style={s.chipWrap}>
                {VM_DEPS_OPTIONS.map(dep => {
                  const on = env.vmDeps.includes(dep)
                  return (
                    <TouchableOpacity
                      key={dep}
                      style={[s.chip, on && s.chipActive]}
                      onPress={() => toggleItem(env.vmDeps, dep, 'vmDeps')}
                    >
                      <Text style={[s.chipText, on && s.chipTextActive]}>{dep}</Text>
                    </TouchableOpacity>
                  )
                })}
              </View>

              <Text style={s.subLabel}>INCLUDE MOUNTS</Text>
              <View style={s.chipWrap}>
                {VM_INCLUDES_OPTIONS.map(inc => {
                  const on = env.vmIncludes.includes(inc)
                  return (
                    <TouchableOpacity
                      key={inc}
                      style={[s.chip, on && s.chipActive]}
                      onPress={() => toggleItem(env.vmIncludes, inc, 'vmIncludes')}
                    >
                      <Text style={[s.chipText, on && s.chipTextActive]}>{inc}</Text>
                    </TouchableOpacity>
                  )
                })}
              </View>

              <Text style={s.subLabel}>MCP AGENTS</Text>
              <View style={s.chipWrap}>
                {VM_MCP_OPTIONS.map(mcp => {
                  const on = env.vmMcpAgents.includes(mcp)
                  return (
                    <TouchableOpacity
                      key={mcp}
                      style={[s.chip, on && s.chipActive]}
                      onPress={() => toggleItem(env.vmMcpAgents, mcp, 'vmMcpAgents')}
                    >
                      <Text style={[s.chipText, on && s.chipTextActive]}>{mcp}</Text>
                    </TouchableOpacity>
                  )
                })}
              </View>

              <TouchableOpacity
                style={[s.startVmBtn, vmBootState === 'running' && s.startVmBtnRunning]}
                onPress={handleStartVm}
                activeOpacity={0.85}
              >
                <Text style={[s.startVmText, vmBootState === 'running' && { color: T.green }]}>
                  {vmBootState === 'idle'
                    ? 'Start VM (Demo)'
                    : vmBootState === 'booting'
                      ? vmBootStep
                      : `● ${vmBootStep}`}
                </Text>
              </TouchableOpacity>
            </View>
          )}
        </View>

        {/* Connectors & Notifications */}
        <Text style={s.sectionLabel}>CONNECTORS & ALERTS</Text>
        <View style={s.card}>
          <View style={s.rowBetween}>
            <View>
              <Text style={s.rowTitle}>Google Calendar & Gmail</Text>
              <Text style={s.rowSub}>
                {googleConnected ? 'Connected · Calendar & Reminders active' : 'Disconnected'}
              </Text>
            </View>
            <Switch
              value={googleConnected}
              onValueChange={setGoogleConnected}
              trackColor={{ false: T.border, true: T.indigo + '88' }}
              thumbColor={googleConnected ? T.indigo : T.textDim}
            />
          </View>
          <View style={s.divider} />
          <View style={s.rowBetween}>
            <View>
              <Text style={s.rowTitle}>Approval prompts</Text>
              <Text style={s.rowSub}>Pause before external form submit or send</Text>
            </View>
            <Switch
              value={approvalNotifs}
              onValueChange={setApprovalNotifs}
              trackColor={{ false: T.border, true: T.indigo + '88' }}
              thumbColor={approvalNotifs ? T.indigo : T.textDim}
            />
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: T.bg },
  content: { padding: 16, paddingBottom: 44 },

  header: { marginBottom: 18, marginTop: 4 },
  heading: { color: T.text, fontSize: 24, fontWeight: '700', letterSpacing: -0.4 },
  sub: { color: T.textDim, fontSize: 12, marginTop: 2 },

  sectionLabel: {
    color: T.textDim, fontSize: 10, fontWeight: '700', letterSpacing: 1,
    marginBottom: 8, marginTop: 14,
  },
  subLabel: { color: T.textDim, fontSize: 10, fontWeight: '700', letterSpacing: 0.8, marginTop: 4 },

  card: {
    backgroundColor: T.surface, borderRadius: 16,
    borderWidth: 1, borderColor: T.border, padding: 14, gap: 10,
  },
  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10 },
  rowTitle: { color: T.text, fontSize: 14, fontWeight: '600' },
  rowSub: { color: T.textDim, fontSize: 12, marginTop: 2 },
  divider: { height: 1, backgroundColor: T.border },

  logoutBtn: {
    backgroundColor: T.redDim, borderWidth: 1, borderColor: T.red + '44',
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 10,
  },
  logoutText: { color: T.red, fontSize: 12, fontWeight: '700' },

  modeRow: { flexDirection: 'row', gap: 8 },
  modeBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 7,
    backgroundColor: T.elevated, borderWidth: 1, borderColor: T.border,
    paddingVertical: 10, borderRadius: 12,
  },
  modeBtnActive: { borderColor: T.indigo, backgroundColor: T.indigoDim },
  modeText: { color: T.textSub, fontSize: 13, fontWeight: '600' },
  modeTextActive: { color: T.indigo, fontWeight: '700' },
  envNote: { color: T.textSub, fontSize: 12 },

  chipWrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 7 },
  chip: {
    backgroundColor: T.elevated, borderWidth: 1, borderColor: T.border,
    borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6,
  },
  chipActive: { borderColor: T.indigo, backgroundColor: T.indigoDim },
  chipText: { color: T.textSub, fontSize: 12 },
  chipTextActive: { color: T.indigo, fontWeight: '600' },

  startVmBtn: {
    marginTop: 8, backgroundColor: T.indigo, borderRadius: 12,
    paddingVertical: 12, alignItems: 'center',
  },
  startVmBtnRunning: {
    backgroundColor: T.greenDim, borderWidth: 1, borderColor: T.green + '55',
  },
  startVmText: { color: '#141413', fontSize: 13, fontWeight: '700' },
})
