import React, { useState } from 'react'
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  SafeAreaView, StatusBar, Switch,
} from 'react-native'
import { T, PulseDot } from '../components/ui'

export function SettingsScreen() {
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

  const metaRows = [
    { label: 'Version', value: 'demo-0.1.0' },
    { label: 'Build',   value: 'hackathon-2025' },
    { label: 'Gateway', value: 'MCP + policy.yaml' },
    { label: 'Auth',    value: 'Bearer token' },
    { label: 'Profile', value: 'coding / general / daily_routine' },
  ]

  return (
    <SafeAreaView style={s.safe}>
      <StatusBar barStyle="light-content" />
      <ScrollView contentContainerStyle={s.content} showsVerticalScrollIndicator={false}>

        {/* Header */}
        <View style={s.header}>
          <Text style={s.heading}>Settings</Text>
          <Text style={s.sub}>Control plane & integrations</Text>
        </View>

        {/* Server connection */}
        <SectionTitle label="Control Plane" />
        <View style={s.card}>
          <Row label="Server URL">
            <Text style={s.valueText}>localhost:8000</Text>
          </Row>
          <Divider />
          <Row label="Auth">
            <Text style={s.valueText}>Bearer ••••••</Text>
          </Row>
          <Divider />
          <View style={s.connRow}>
            <View style={s.connDot}>
              <PulseDot color={T.green} size={7} />
            </View>
            <Text style={s.connText}>Connected · demo mode</Text>
            <View style={{ flex: 1 }} />
            <TouchableOpacity
              style={s.saveBtn}
              onPress={() => { setSaved(true); setTimeout(() => setSaved(false), 2000) }}
              activeOpacity={0.8}
            >
              <Text style={s.saveBtnText}>{saved ? '✓ Saved' : 'Save'}</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Google */}
        <SectionTitle label="Google Connector" badge="DEMO" />
        <View style={s.card}>
          <View style={s.googleRow}>
            {/* Google G mark — real SVG-like using text */}
            <View style={s.googleMark}>
              <Text style={s.googleG}>G</Text>
            </View>

            <View style={{ flex: 1, gap: 2 }}>
              <Text style={s.googleTitle}>Calendar & Gmail</Text>
              <Text style={s.googleSub}>
                {googleConnected
                  ? 'Connected — daily_routine profile active'
                  : 'Enables the daily_routine agent profile'}
              </Text>
            </View>

            <TouchableOpacity
              style={[s.connectBtn, googleConnected && s.connectBtnDanger]}
              onPress={googleConnected ? () => setGoogleConnected(false) : connectGoogle}
              disabled={connecting}
              activeOpacity={0.8}
            >
              <Text style={[s.connectBtnText, googleConnected && { color: T.red }]}>
                {connecting ? '…' : googleConnected ? 'Disconnect' : 'Connect'}
              </Text>
            </TouchableOpacity>
          </View>

          {googleConnected && (
            <View style={s.permsList}>
              {[
                '✓  Calendar — read access',
                '✓  Gmail — compose drafts only',
                '·   Send requires explicit user tap',
                '·   Tokens revoked on session End',
              ].map((line, i) => (
                <Text key={i} style={[s.permLine, line.startsWith('✓') && { color: T.green + 'cc' }]}>
                  {line}
                </Text>
              ))}
            </View>
          )}
        </View>

        {/* Notifications */}
        <SectionTitle label="Notifications" />
        <View style={s.card}>
          <View style={s.switchRow}>
            <View>
              <Text style={s.switchTitle}>Approval alerts</Text>
              <Text style={s.switchDesc}>Notify when the agent needs your tap</Text>
            </View>
            <Switch
              value={approvalNotifs}
              onValueChange={setApprovalNotifs}
              trackColor={{ false: T.border, true: T.indigo }}
              thumbColor="#fff"
              ios_backgroundColor={T.border}
            />
          </View>
          <Divider />
          <View style={[s.switchRow, { opacity: 0.4 }]}>
            <View>
              <Text style={s.switchTitle}>Session updates</Text>
              <Text style={s.switchDesc}>State changes and completions</Text>
            </View>
            <Switch
              value={false}
              trackColor={{ false: T.border, true: T.indigo }}
              thumbColor="#fff"
            />
          </View>
        </View>

        {/* Gateway policy */}
        <SectionTitle label="MCP Gateway" />
        <View style={s.card}>
          {[
            { icon: '✓', label: 'Policy engine', value: 'allow / ask / block', color: T.green },
            { icon: '◫', label: 'Path jail', value: '/workspace only', color: T.indigo },
            { icon: '◎', label: 'Egress proxy', value: 'allowlist enforced', color: T.indigo },
            { icon: '⌛', label: 'Tool timeout', value: '30s default', color: T.textSub },
            { icon: '◈', label: 'Audit chain', value: 'SHA-256 hash-chained', color: T.textSub },
          ].map((row, i, arr) => (
            <View key={row.label}>
              <View style={s.policyRow}>
                <Text style={[s.policyIcon, { color: row.color }]}>{row.icon}</Text>
                <Text style={s.policyLabel}>{row.label}</Text>
                <Text style={s.policyValue}>{row.value}</Text>
              </View>
              {i < arr.length - 1 && <Divider />}
            </View>
          ))}
        </View>

        {/* About */}
        <SectionTitle label="About" />
        <View style={s.card}>
          {metaRows.map((row, i) => (
            <View key={row.label}>
              <Row label={row.label}>
                <Text style={s.valueText}>{row.value}</Text>
              </Row>
              {i < metaRows.length - 1 && <Divider />}
            </View>
          ))}
        </View>

        {/* Tagline */}
        <Text style={s.tagline}>
          Overwatch — Sandboxed MCP agents{'\n'}
          with a policy gateway and remote control
        </Text>
      </ScrollView>
    </SafeAreaView>
  )
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function SectionTitle({ label, badge }: { label: string; badge?: string }) {
  return (
    <View style={s.sectionRow}>
      <Text style={s.sectionLabel}>{label.toUpperCase()}</Text>
      {badge && (
        <View style={s.sectionBadge}>
          <Text style={s.sectionBadgeText}>{badge}</Text>
        </View>
      )}
    </View>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={s.row}>
      <Text style={s.rowLabel}>{label}</Text>
      {children}
    </View>
  )
}

function Divider() {
  return <View style={s.divider} />
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: T.bg },
  content: { paddingBottom: 70 },

  header: { paddingHorizontal: 24, paddingTop: 20, paddingBottom: 20 },
  heading: { color: T.text, fontSize: 26, fontWeight: '800', letterSpacing: -0.8 },
  sub: { color: T.textDim, fontSize: 13, marginTop: 2 },

  sectionRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 20, paddingTop: 20, paddingBottom: 10,
  },
  sectionLabel: {
    color: T.textDim, fontSize: 11, fontWeight: '700', letterSpacing: 1.5,
  },
  sectionBadge: {
    backgroundColor: T.violetDim, borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2,
    borderWidth: 1, borderColor: T.violet + '40',
  },
  sectionBadgeText: { color: T.violet, fontSize: 9, fontWeight: '800' },

  card: {
    marginHorizontal: 16, backgroundColor: T.surface,
    borderRadius: 18, borderWidth: 1, borderColor: T.border,
    overflow: 'hidden',
  },

  row: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 13,
  },
  rowLabel: { color: T.textSub, fontSize: 13 },
  valueText: { color: T.textDim, fontSize: 12, fontFamily: T.mono },
  divider: { height: 1, backgroundColor: T.border, marginHorizontal: 16 },

  connRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 16, paddingVertical: 10,
  },
  connDot: { width: 20, alignItems: 'center' },
  connText: { color: T.green, fontSize: 12 },
  saveBtn: {
    backgroundColor: T.indigoDim, borderWidth: 1, borderColor: T.indigo + '40',
    borderRadius: 8, paddingHorizontal: 12, paddingVertical: 6,
  },
  saveBtnText: { color: T.indigo, fontSize: 12, fontWeight: '700' },

  googleRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12, padding: 16,
  },
  googleMark: {
    width: 38, height: 38, borderRadius: 10, backgroundColor: '#fff',
    alignItems: 'center', justifyContent: 'center',
  },
  googleG: { fontSize: 22, fontWeight: '900', color: '#4285F4' },
  googleTitle: { color: T.text, fontSize: 14, fontWeight: '600' },
  googleSub: { color: T.textDim, fontSize: 11 },
  connectBtn: {
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
    borderRadius: 10, paddingHorizontal: 10, paddingVertical: 6,
  },
  connectBtnDanger: { borderColor: T.red + '40', backgroundColor: T.redDim },
  connectBtnText: { color: T.textSub, fontSize: 12, fontWeight: '700' },
  permsList: {
    borderTopWidth: 1, borderTopColor: T.border,
    padding: 14, gap: 5,
  },
  permLine: { color: T.textDim, fontSize: 11, fontFamily: T.mono },

  switchRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    padding: 16, gap: 16,
  },
  switchTitle: { color: T.text, fontSize: 14, fontWeight: '500' },
  switchDesc: { color: T.textDim, fontSize: 11, marginTop: 2 },

  policyRow: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    paddingHorizontal: 16, paddingVertical: 12,
  },
  policyIcon: { fontSize: 14, width: 18, textAlign: 'center' },
  policyLabel: { color: T.textSub, fontSize: 13, flex: 1 },
  policyValue: { color: T.textDim, fontSize: 11, fontFamily: T.mono },

  tagline: {
    color: T.textFaint, fontSize: 11, textAlign: 'center',
    lineHeight: 18, marginTop: 32, marginBottom: 20,
  },
})
