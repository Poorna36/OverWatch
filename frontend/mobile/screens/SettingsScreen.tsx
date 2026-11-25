import React, { useState } from 'react'
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  SafeAreaView, StatusBar, Switch,
} from 'react-native'
import { C } from '../components/ui'

interface Row { label: string; value: string }

export function SettingsScreen() {
  const [serverUrl, setServerUrl] = useState('http://localhost:8000')
  const [token] = useState('demo-token-hackathon-2025')
  const [googleConnected, setGoogleConnected] = useState(false)
  const [approvalNotifs, setApprovalNotifs] = useState(true)
  const [connecting, setConnecting] = useState(false)
  const [saved, setSaved] = useState(false)

  async function connectGoogle() {
    setConnecting(true)
    await new Promise(r => setTimeout(r, 1400))
    setGoogleConnected(true)
    setConnecting(false)
  }

  function save() {
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  const metaRows: Row[] = [
    { label: 'Version', value: 'demo-0.1.0' },
    { label: 'Build', value: 'hackathon-2025' },
    { label: 'Gateway', value: 'MCP + policy.yaml' },
    { label: 'Auth', value: 'Bearer token' },
  ]

  return (
    <SafeAreaView style={s.safe}>
      <StatusBar barStyle="light-content" backgroundColor={C.bg} />
      <ScrollView style={s.scroll} contentContainerStyle={s.content}>

        {/* Header */}
        <View style={s.header}>
          <Text style={s.heading}>Settings</Text>
          <Text style={s.sub}>Configure your connection</Text>
        </View>

        {/* Server */}
        <SectionLabel>CONTROL PLANE</SectionLabel>
        <View style={s.card}>
          <Field label="Server URL">
            <Text style={s.fieldValue}>{serverUrl}</Text>
          </Field>
          <Divider />
          <Field label="Bearer token">
            <Text style={s.fieldValue}>{'•'.repeat(20)}</Text>
          </Field>
          <Divider />
          <View style={s.connectedRow}>
            <View style={s.connectedDot} />
            <Text style={s.connectedText}>Connected (demo mode)</Text>
            <View style={{ flex: 1 }} />
            <TouchableOpacity style={s.saveBtn} onPress={save} activeOpacity={0.8}>
              <Text style={s.saveBtnText}>{saved ? '✓ Saved' : 'Save'}</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Google */}
        <SectionLabel>
          GOOGLE CONNECTOR{'  '}
          <Text style={s.demoBadge}>DEMO</Text>
        </SectionLabel>
        <View style={s.card}>
          <View style={s.googleRow}>
            {/* Google G */}
            <View style={s.googleIcon}>
              <Text style={s.googleIconText}>G</Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={s.googleTitle}>Calendar & Gmail</Text>
              <Text style={s.googleSub}>
                {googleConnected
                  ? 'Connected · daily_routine profile active'
                  : 'Enable the daily_routine profile'}
              </Text>
            </View>
            <TouchableOpacity
              style={[s.connectBtn, googleConnected && s.connectBtnDanger]}
              onPress={googleConnected ? () => setGoogleConnected(false) : connectGoogle}
              disabled={connecting}
              activeOpacity={0.8}
            >
              <Text style={[s.connectBtnText, googleConnected && s.connectBtnTextDanger]}>
                {connecting ? '…' : googleConnected ? 'Disconnect' : 'Connect'}
              </Text>
            </TouchableOpacity>
          </View>
          {googleConnected && (
            <View style={s.googlePerms}>
              <Text style={s.googlePerm}>✓ Calendar: read access</Text>
              <Text style={s.googlePerm}>✓ Gmail: drafts only (send requires tap)</Text>
              <Text style={s.googlePerm}>· Tokens revoked on session End</Text>
            </View>
          )}
        </View>

        {/* Notifications */}
        <SectionLabel>NOTIFICATIONS</SectionLabel>
        <View style={s.card}>
          <View style={s.toggleRow}>
            <View>
              <Text style={s.toggleTitle}>Approval alerts</Text>
              <Text style={s.toggleSub}>Notify when agent needs your tap</Text>
            </View>
            <Switch
              value={approvalNotifs}
              onValueChange={setApprovalNotifs}
              trackColor={{ false: C.border, true: C.indigo }}
              thumbColor="#fff"
            />
          </View>
        </View>

        {/* About */}
        <SectionLabel>ABOUT</SectionLabel>
        <View style={s.card}>
          {metaRows.map((row, i) => (
            <View key={row.label}>
              <View style={s.metaRow}>
                <Text style={s.metaKey}>{row.label}</Text>
                <Text style={s.metaVal}>{row.value}</Text>
              </View>
              {i < metaRows.length - 1 && <Divider />}
            </View>
          ))}
        </View>

        <Text style={s.fine}>
          Overwatch — demo build for hackathon{'\n'}
          Sandboxed MCP agents with policy gateway
        </Text>
      </ScrollView>
    </SafeAreaView>
  )
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <Text style={s.sectionLabel}>{children}</Text>
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={s.fieldRow}>
      <Text style={s.fieldLabel}>{label}</Text>
      {children}
    </View>
  )
}

function Divider() {
  return <View style={s.divider} />
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.bg },
  scroll: { flex: 1 },
  content: { paddingBottom: 60 },

  header: { paddingHorizontal: 20, paddingTop: 20, paddingBottom: 16 },
  heading: { color: C.text, fontSize: 26, fontWeight: '700', letterSpacing: -0.5 },
  sub: { color: C.textMuted, fontSize: 13, marginTop: 2 },

  sectionLabel: {
    color: C.textMuted, fontSize: 10, fontWeight: '700', letterSpacing: 1.5,
    paddingHorizontal: 20, paddingTop: 20, paddingBottom: 8,
  },
  demoBadge: { color: C.accent, fontSize: 10 },

  card: {
    marginHorizontal: 16, backgroundColor: C.surface,
    borderWidth: 1, borderColor: C.border, borderRadius: 14,
  },

  fieldRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 14, paddingVertical: 12,
  },
  fieldLabel: { color: C.textMuted, fontSize: 13 },
  fieldValue: { color: C.text, fontSize: 13, fontFamily: 'monospace' },

  divider: { height: 1, backgroundColor: C.border, marginHorizontal: 14 },

  connectedRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 14, paddingVertical: 10,
  },
  connectedDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: C.green },
  connectedText: { color: C.green, fontSize: 12 },
  saveBtn: {
    backgroundColor: C.indigo, paddingHorizontal: 12, paddingVertical: 6,
    borderRadius: 8,
  },
  saveBtnText: { color: '#fff', fontSize: 12, fontWeight: '600' },

  googleRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14,
  },
  googleIcon: {
    width: 36, height: 36, borderRadius: 10, backgroundColor: '#fff',
    alignItems: 'center', justifyContent: 'center',
  },
  googleIconText: { fontSize: 20, fontWeight: '700', color: '#4285F4' },
  googleTitle: { color: C.text, fontSize: 14, fontWeight: '500' },
  googleSub: { color: C.textMuted, fontSize: 11, marginTop: 2 },
  connectBtn: {
    backgroundColor: C.surface2, borderWidth: 1, borderColor: C.border,
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8,
  },
  connectBtnDanger: { borderColor: '#f8717140', backgroundColor: '#450a0a22' },
  connectBtnText: { color: C.textMuted, fontSize: 12, fontWeight: '600' },
  connectBtnTextDanger: { color: C.red },
  googlePerms: {
    borderTopWidth: 1, borderTopColor: C.border,
    padding: 14, gap: 4,
  },
  googlePerm: { color: C.textMuted, fontSize: 11, fontFamily: 'monospace' },

  toggleRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    padding: 14,
  },
  toggleTitle: { color: C.text, fontSize: 14, fontWeight: '500' },
  toggleSub: { color: C.textMuted, fontSize: 11, marginTop: 2 },

  metaRow: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingHorizontal: 14, paddingVertical: 11,
  },
  metaKey: { color: C.textMuted, fontSize: 12, fontFamily: 'monospace' },
  metaVal: { color: C.text, fontSize: 12, fontFamily: 'monospace' },

  fine: { color: C.textSubtle, fontSize: 11, textAlign: 'center', marginTop: 32, lineHeight: 18 },
})
