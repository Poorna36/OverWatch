import React, { useState } from 'react'
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  TextInput, StatusBar, KeyboardAvoidingView, Platform,
} from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import {
  type EnvConfig, VM_IMAGES, VM_DEPS_OPTIONS, VM_INCLUDES_OPTIONS, VM_MCP_OPTIONS,
} from '../lib/mock-data'
import { T, PulseDot } from '../components/ui'

interface LoginScreenProps {
  env: EnvConfig
  onChangeEnv: (env: EnvConfig) => void
  onComplete: (email: string) => void
}

export function LoginScreen({ env, onChangeEnv, onComplete }: LoginScreenProps) {
  const [step, setStep] = useState<'login' | 'connect'>('login')
  const [email, setEmail] = useState('poorn@overwatch.dev')
  const [password, setPassword] = useState('••••••••••••')
  const [loading, setLoading] = useState(false)

  function toggleItem(list: string[], item: string, key: 'vmDeps' | 'vmIncludes' | 'vmMcpAgents') {
    const next = list.includes(item) ? list.filter(x => x !== item) : [...list, item]
    onChangeEnv({ ...env, [key]: next })
  }

  async function handleLogin() {
    setLoading(true)
    await new Promise(r => setTimeout(r, 450))
    setLoading(false)
    setStep('connect')
  }

  async function handleConnect() {
    setLoading(true)
    await new Promise(r => setTimeout(r, 500))
    setLoading(false)
    onComplete(email.trim() || 'poorn@overwatch.dev')
  }

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <StatusBar barStyle="light-content" />
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <ScrollView
          contentContainerStyle={s.scroll}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          {/* Brand */}
          <View style={s.brandRow}>
            <View style={s.logoMark}>
              <Text style={s.logoGlyph}>OW</Text>
            </View>
            <View>
              <Text style={s.brandTitle}>Overwatch</Text>
              <Text style={s.brandSub}>MCP Control Plane · Demo</Text>
            </View>
          </View>

          {step === 'login' ? (
            <View style={s.card}>
              <Text style={s.title}>Sign in to continue</Text>
              <Text style={s.subtitle}>
                Dummy auth for demo — connect to your Remote Desktop or launch an isolated VM.
              </Text>

              <Text style={s.label}>EMAIL</Text>
              <TextInput
                value={email}
                onChangeText={setEmail}
                placeholder="you@overwatch.dev"
                placeholderTextColor={T.textFaint}
                autoCapitalize="none"
                keyboardType="email-address"
                style={s.input}
              />

              <Text style={s.label}>PASSWORD</Text>
              <TextInput
                value={password}
                onChangeText={setPassword}
                placeholder="••••••••"
                placeholderTextColor={T.textFaint}
                secureTextEntry
                style={s.input}
              />

              <TouchableOpacity
                style={[s.primaryBtn, loading && { opacity: 0.6 }]}
                onPress={handleLogin}
                disabled={loading}
                activeOpacity={0.85}
              >
                <Text style={s.primaryBtnText}>
                  {loading ? 'Signing in…' : 'Continue →'}
                </Text>
              </TouchableOpacity>

              <View style={s.dividerRow}>
                <View style={s.dividerLine} />
                <Text style={s.dividerText}>or</Text>
                <View style={s.dividerLine} />
              </View>

              <TouchableOpacity
                style={s.secondaryBtn}
                onPress={handleLogin}
                activeOpacity={0.8}
              >
                <Text style={s.secondaryBtnText}>G   Continue with Google (Demo)</Text>
              </TouchableOpacity>
            </View>
          ) : (
            <View style={s.connectWrap}>
              <Text style={s.title}>Choose workspace</Text>
              <Text style={s.subtitle}>
                Select where the agent executes tools. You can switch anytime from Settings.
              </Text>

              {/* Option 1: Remote Desktop */}
              <TouchableOpacity
                style={[s.envCard, env.mode === 'desktop' && s.envCardActive]}
                onPress={() => onChangeEnv({ ...env, mode: 'desktop' })}
                activeOpacity={0.85}
              >
                <View style={s.envHeader}>
                  <View style={s.envTitleRow}>
                    <PulseDot color={env.mode === 'desktop' ? T.green : T.textDim} size={8} />
                    <Text style={s.envTitle}>Remote Desktop</Text>
                    <View style={s.recBadge}>
                      <Text style={s.recBadgeText}>ACTIVE DEMO</Text>
                    </View>
                  </View>
                  <View style={[s.radio, env.mode === 'desktop' && s.radioActive]}>
                    {env.mode === 'desktop' && <View style={s.radioDot} />}
                  </View>
                </View>
                <Text style={s.envDesc}>
                  Direct RDP bridge to <Text style={{ color: T.text }}>{env.desktopHost}</Text>. Best for calendar, browser forms & local Python workspace.
                </Text>
              </TouchableOpacity>

              {/* Option 2: Isolated VM */}
              <TouchableOpacity
                style={[s.envCard, env.mode === 'vm' && s.envCardActive]}
                onPress={() => onChangeEnv({ ...env, mode: 'vm' })}
                activeOpacity={0.85}
              >
                <View style={s.envHeader}>
                  <View style={s.envTitleRow}>
                    <View style={[s.vmDot, { backgroundColor: env.mode === 'vm' ? T.indigo : T.textDim }]} />
                    <Text style={s.envTitle}>Isolated VM Sandbox</Text>
                  </View>
                  <View style={[s.radio, env.mode === 'vm' && s.radioActive]}>
                    {env.mode === 'vm' && <View style={s.radioDot} />}
                  </View>
                </View>
                <Text style={s.envDesc}>
                  Ephemeral container with selectable OS image, dependencies, mounts & MCP agents.
                </Text>
              </TouchableOpacity>

              {/* VM Configuration options (shown when VM selected) */}
              {env.mode === 'vm' && (
                <View style={s.vmConfig}>
                  <Text style={s.vmSectionLabel}>BASE IMAGE</Text>
                  <View style={s.imageList}>
                    {VM_IMAGES.map(img => {
                      const active = env.vmImage === img.id
                      return (
                        <TouchableOpacity
                          key={img.id}
                          style={[s.imageRow, active && s.imageRowActive]}
                          onPress={() => onChangeEnv({ ...env, vmImage: img.id })}
                          activeOpacity={0.8}
                        >
                          <View style={{ flex: 1 }}>
                            <Text style={[s.imageName, active && { color: T.indigo }]}>{img.label}</Text>
                            <Text style={s.imageDesc}>{img.desc}</Text>
                          </View>
                          <Text style={[s.checkMark, { opacity: active ? 1 : 0 }]}>✓</Text>
                        </TouchableOpacity>
                      )
                    })}
                  </View>

                  <Text style={s.vmSectionLabel}>DEPENDENCIES</Text>
                  <View style={s.chipWrap}>
                    {VM_DEPS_OPTIONS.map(dep => {
                      const on = env.vmDeps.includes(dep)
                      return (
                        <TouchableOpacity
                          key={dep}
                          style={[s.chip, on && s.chipActive]}
                          onPress={() => toggleItem(env.vmDeps, dep, 'vmDeps')}
                        >
                          <Text style={[s.chipText, on && s.chipTextActive]}>{on ? '✓ ' : '+ '}{dep}</Text>
                        </TouchableOpacity>
                      )
                    })}
                  </View>

                  <Text style={s.vmSectionLabel}>INCLUDE IN ENVIRONMENT</Text>
                  <View style={s.chipWrap}>
                    {VM_INCLUDES_OPTIONS.map(inc => {
                      const on = env.vmIncludes.includes(inc)
                      return (
                        <TouchableOpacity
                          key={inc}
                          style={[s.chip, on && s.chipActive]}
                          onPress={() => toggleItem(env.vmIncludes, inc, 'vmIncludes')}
                        >
                          <Text style={[s.chipText, on && s.chipTextActive]}>{on ? '✓ ' : '+ '}{inc}</Text>
                        </TouchableOpacity>
                      )
                    })}
                  </View>

                  <Text style={s.vmSectionLabel}>MCP AGENTS</Text>
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
                </View>
              )}

              <TouchableOpacity
                style={[s.primaryBtn, { marginTop: 18 }, loading && { opacity: 0.6 }]}
                onPress={handleConnect}
                disabled={loading}
                activeOpacity={0.85}
              >
                <Text style={s.primaryBtnText}>
                  {loading
                    ? 'Connecting…'
                    : env.mode === 'desktop'
                      ? 'Connect to Desktop & Open Chat →'
                      : 'Provision VM & Open Chat →'}
                </Text>
              </TouchableOpacity>
            </View>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  )
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: T.bg },
  scroll: { padding: 20, paddingTop: 36, paddingBottom: 48 },

  brandRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 28 },
  logoMark: {
    width: 42, height: 42, borderRadius: 12,
    backgroundColor: T.indigoDim, borderWidth: 1, borderColor: T.indigo + '55',
    alignItems: 'center', justifyContent: 'center',
  },
  logoGlyph: { color: T.indigo, fontSize: 20 },
  brandTitle: { color: T.text, fontSize: 20, fontWeight: '700', letterSpacing: -0.3 },
  brandSub: { color: T.textDim, fontSize: 12, marginTop: 1 },

  card: {
    backgroundColor: T.surface, borderRadius: 20,
    borderWidth: 1, borderColor: T.border, padding: 20,
  },
  connectWrap: { gap: 10 },
  title: { color: T.text, fontSize: 22, fontWeight: '700', letterSpacing: -0.4, marginBottom: 6 },
  subtitle: { color: T.textSub, fontSize: 13, lineHeight: 19, marginBottom: 16 },

  label: { color: T.textDim, fontSize: 10, fontWeight: '700', letterSpacing: 1, marginBottom: 6, marginTop: 10 },
  input: {
    backgroundColor: T.elevated, borderWidth: 1, borderColor: T.border,
    borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12,
    color: T.text, fontSize: 14,
  },

  primaryBtn: {
    backgroundColor: T.indigo, borderRadius: 14,
    paddingVertical: 15, alignItems: 'center', marginTop: 18,
  },
  primaryBtnText: { color: '#141413', fontSize: 15, fontWeight: '700' },

  dividerRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginVertical: 16 },
  dividerLine: { flex: 1, height: 1, backgroundColor: T.border },
  dividerText: { color: T.textDim, fontSize: 12 },

  secondaryBtn: {
    backgroundColor: T.elevated, borderWidth: 1, borderColor: T.border,
    borderRadius: 14, paddingVertical: 13, alignItems: 'center',
  },
  secondaryBtnText: { color: T.text, fontSize: 14, fontWeight: '600' },

  envCard: {
    backgroundColor: T.surface, borderRadius: 16,
    borderWidth: 1, borderColor: T.border, padding: 16,
  },
  envCardActive: { borderColor: T.indigo, backgroundColor: T.indigoDim + '55' },
  envHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 },
  envTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 8, flex: 1 },
  envTitle: { color: T.text, fontSize: 15, fontWeight: '700' },
  envDesc: { color: T.textSub, fontSize: 12, lineHeight: 18 },
  vmDot: { width: 8, height: 8, borderRadius: 4 },
  recBadge: {
    backgroundColor: T.greenDim, borderWidth: 1, borderColor: T.green + '44',
    paddingHorizontal: 7, paddingVertical: 2, borderRadius: 6,
  },
  recBadgeText: { color: T.green, fontSize: 9, fontWeight: '800', letterSpacing: 0.6 },

  radio: {
    width: 18, height: 18, borderRadius: 9, borderWidth: 1.5, borderColor: T.borderMid,
    alignItems: 'center', justifyContent: 'center',
  },
  radioActive: { borderColor: T.indigo },
  radioDot: { width: 9, height: 9, borderRadius: 5, backgroundColor: T.indigo },

  vmConfig: {
    backgroundColor: T.surface, borderRadius: 16,
    borderWidth: 1, borderColor: T.border, padding: 14, marginTop: 4,
  },
  vmSectionLabel: {
    color: T.textDim, fontSize: 10, fontWeight: '700', letterSpacing: 1,
    marginTop: 10, marginBottom: 8,
  },
  imageList: { gap: 6 },
  imageRow: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    backgroundColor: T.elevated, borderRadius: 10, borderWidth: 1, borderColor: T.border,
    paddingHorizontal: 12, paddingVertical: 9,
  },
  imageRowActive: { borderColor: T.indigo + '88', backgroundColor: T.indigoDim },
  imageName: { color: T.text, fontSize: 13, fontWeight: '600' },
  imageDesc: { color: T.textDim, fontSize: 11, marginTop: 1 },
  checkMark: { color: T.indigo, fontSize: 14, fontWeight: '700' },

  chipWrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 7 },
  chip: {
    backgroundColor: T.elevated, borderWidth: 1, borderColor: T.border,
    borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6,
  },
  chipActive: { borderColor: T.indigo, backgroundColor: T.indigoDim },
  chipText: { color: T.textSub, fontSize: 12, fontWeight: '500' },
  chipTextActive: { color: T.indigo, fontWeight: '600' },
})
