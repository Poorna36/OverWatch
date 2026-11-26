import React, { useState } from 'react'
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput, StatusBar, Modal,
} from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import {
  EXTRA_IMAGE_PACKAGES,
  type EnvConfig, type PermissionLevel, type ScopeToolItem,
} from '../lib/mock-data'
import { T, PulseDot } from '../components/ui'

interface TasksScreenProps {
  scopeTools: ScopeToolItem[]
  onUpdateScopeTools: React.Dispatch<React.SetStateAction<ScopeToolItem[]>>
  env: EnvConfig
}

const PERM_LABELS: Record<PermissionLevel, { label: string; color: string }> = {
  allow_always:   { label: 'Allow always', color: T.green },
  ask_every_time: { label: 'Ask every time', color: T.amber },
  read_only:      { label: 'Read only', color: T.blue },
  deny:           { label: 'Deny', color: T.red },
}

const PERM_ORDER: PermissionLevel[] = ['allow_always', 'ask_every_time', 'read_only', 'deny']

export function TasksScreen({ scopeTools, onUpdateScopeTools, env }: TasksScreenProps) {
  const [filter, setFilter] = useState<'all' | 'active' | 'stopped'>('all')
  const [aiAlertMsg, setAiAlertMsg] = useState<string | null>(null)
  const [newPkg, setNewPkg] = useState('')
  const [imageSha, setImageSha] = useState('sha256:9f4a82c1')
  const [rebuilding, setRebuilding] = useState(false)
  const [buildLogs, setBuildLogs] = useState<string[]>([])

  const inUseCount = scopeTools.filter(t => t.inUse && !t.stopped).length
  const stoppedCount = scopeTools.filter(t => t.stopped).length

  const visibleTools = scopeTools.filter(t => {
    if (filter === 'active') return t.inUse && !t.stopped
    if (filter === 'stopped') return t.stopped
    return true
  })

  function cyclePermission(id: string) {
    onUpdateScopeTools(prev =>
      prev.map(item => {
        if (item.id !== id) return item
        const nextIdx = (PERM_ORDER.indexOf(item.permission) + 1) % PERM_ORDER.length
        const nextPerm = PERM_ORDER[nextIdx]
        return { ...item, permission: nextPerm }
      })
    )
  }

  function toggleStop(id: string) {
    const target = scopeTools.find(t => t.id === id)
    if (!target) return
    const nextStopped = !target.stopped

    onUpdateScopeTools(prev =>
      prev.map(item =>
        item.id === id
          ? { ...item, stopped: nextStopped, inUse: nextStopped ? false : item.inUse }
          : item
      )
    )

    if (nextStopped && id === 'gmail') {
      setAiAlertMsg('Gmail access not available by MCP agent — stopped immediately by user policy.')
    } else if (nextStopped) {
      setAiAlertMsg(`${target.name} access stopped by user policy.`)
    }
  }

  function addPackageToImage(pkgName: string) {
    const clean = pkgName.trim().toLowerCase()
    if (!clean) return
    if (scopeTools.some(t => t.name.toLowerCase() === clean)) {
      setNewPkg('')
      return
    }
    const newItem: ScopeToolItem = {
      id: `pkg_${Date.now()}`,
      name: clean,
      category: 'dependency',
      desc: 'Added to container image scope',
      version: 'latest',
      inUse: false,
      stopped: false,
      permission: 'allow_always',
    }
    onUpdateScopeTools(prev => [...prev, newItem])
    setNewPkg('')
  }

  async function handleRebuildImage() {
    if (rebuilding) return
    setRebuilding(true)
    setBuildLogs([`[1/4] FROM overwatch/${env.vmImage}:latest`])
    await new Promise(r => setTimeout(r, 850))
    const deps = scopeTools.filter(t => !t.stopped).map(t => t.name).slice(0, 8).join(', ')
    setBuildLogs(l => [...l, `[2/4] RUN install scope (${deps}...)`])
    await new Promise(r => setTimeout(r, 950))
    setBuildLogs(l => [...l, `[3/4] COPY mcp-policy.yaml /etc/overwatch/policy.yaml`])
    await new Promise(r => setTimeout(r, 850))
    const nextSha = `sha256:${Math.random().toString(16).slice(2, 10)}`
    setImageSha(nextSha)
    setBuildLogs(l => [...l, `[4/4] Built & verified image ${nextSha} [OK]`])
    setRebuilding(false)
  }

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <StatusBar barStyle="light-content" />

      {/* Top Header */}
      <View style={s.header}>
        <Text style={s.heading}>Tools & Permissions</Text>
        <Text style={s.sub}>
          {scopeTools.length} in scope · {inUseCount} active · {stoppedCount} stopped
        </Text>
      </View>

      {/* Scrollable Filter Strip */}
      <View style={s.filterStripWrap}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.filterScroll}>
          {([
            { id: 'all', label: `All Scope (${scopeTools.length})` },
            { id: 'active', label: `In Use (${inUseCount})` },
            { id: 'stopped', label: `Stopped (${stoppedCount})` },
          ] as const).map(f => (
            <TouchableOpacity
              key={f.id}
              style={[s.filterChip, filter === f.id && s.filterChipActive]}
              onPress={() => setFilter(f.id)}
              activeOpacity={0.8}
            >
              <Text style={[s.filterText, filter === f.id && s.filterTextActive]}>
                {f.label}
              </Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
      </View>

      {/* AI Gateway Alert Banner if a tool (especially Gmail) was stopped */}
      {aiAlertMsg && (
        <View style={s.aiBanner}>
          <View style={{ flex: 1 }}>
            <Text style={s.aiBannerTitle}>AI MCP AGENT NOTICE</Text>
            <Text style={s.aiBannerText}>{aiAlertMsg}</Text>
          </View>
          <TouchableOpacity onPress={() => setAiAlertMsg(null)} style={s.aiDismiss}>
            <Text style={s.aiDismissText}>x</Text>
          </TouchableOpacity>
        </View>
      )}

      {/* Main Scroll */}
      <ScrollView contentContainerStyle={s.content} showsVerticalScrollIndicator={false}>
        {/* Quick Horizontal Scope Ribbon */}
        <Text style={s.sectionLabel}>ACTIVE & INSTALLED IN SCOPE</Text>
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={s.ribbonScroll}
        >
          {scopeTools.map(item => (
            <TouchableOpacity
              key={`rib_${item.id}`}
              onPress={() => toggleStop(item.id)}
              style={[
                s.ribbonPill,
                item.stopped ? s.ribbonStopped : item.inUse ? s.ribbonActive : undefined,
              ]}
              activeOpacity={0.8}
            >
              {item.inUse && !item.stopped && <PulseDot color={T.green} size={5} />}
              <Text style={[
                s.ribbonText,
                item.stopped ? { color: T.red } : item.inUse ? { color: T.green } : undefined,
              ]}>
                {item.name}
              </Text>
            </TouchableOpacity>
          ))}
        </ScrollView>

        {/* Detailed Tool & Dependency Control Cards */}
        <View style={s.cardsList}>
          {visibleTools.map(item => {
            const permCfg = PERM_LABELS[item.permission]
            const isActive = item.inUse && !item.stopped
            return (
              <View
                key={item.id}
                style={[
                  s.toolCard,
                  isActive && s.toolCardActive,
                  item.stopped && s.toolCardStopped,
                ]}
              >
                <View style={s.toolTop}>
                  <View style={s.toolTitleWrap}>
                    {isActive && <PulseDot color={T.green} size={6} />}
                    <Text style={[s.toolName, item.stopped && { color: T.red }]}>{item.name}</Text>
                    <Text style={s.catBadge}>{item.category}</Text>
                    <Text style={s.verText}>{item.version}</Text>
                  </View>

                  {isActive && (
                    <View style={s.inUseBadge}>
                      <Text style={s.inUseText}>IN USE</Text>
                    </View>
                  )}
                  {item.stopped && (
                    <View style={s.stoppedBadge}>
                      <Text style={s.stoppedText}>STOPPED</Text>
                    </View>
                  )}
                </View>

                <Text style={s.toolDesc}>{item.desc}</Text>

                {/* Controls: Permission level + Stop/Enable */}
                <View style={s.controlsRow}>
                  <TouchableOpacity
                    style={s.permBtn}
                    onPress={() => cyclePermission(item.id)}
                    activeOpacity={0.8}
                  >
                    <Text style={s.permLabel}>Permission: </Text>
                    <Text style={[s.permVal, { color: permCfg.color }]}>{permCfg.label} ▾</Text>
                  </TouchableOpacity>

                  <TouchableOpacity
                    style={[s.stopBtn, item.stopped && s.resumeBtn]}
                    onPress={() => toggleStop(item.id)}
                    activeOpacity={0.8}
                  >
                    <Text style={[s.stopBtnText, item.stopped && s.resumeBtnText]}>
                      {item.stopped ? 'Resume' : 'Stop'}
                    </Text>
                  </TouchableOpacity>
                </View>
              </View>
            )
          })}
        </View>

        {/* Bottom Section: Docker-style Image Tools & Rebuild */}
        <Text style={[s.sectionLabel, { marginTop: 12 }]}>CONTAINER IMAGE & DEPENDENCIES</Text>
        <View style={s.imageCard}>
          <View style={s.imageHead}>
            <View style={{ flex: 1 }}>
              <Text style={s.imageTitle}>overwatch/{env.vmImage}:latest</Text>
              <Text style={s.imageSha}>Digest: {imageSha} · {scopeTools.filter(t => !t.stopped).length} mounted</Text>
            </View>
            <TouchableOpacity
              style={[s.rebuildBtn, rebuilding && { opacity: 0.6 }]}
              onPress={handleRebuildImage}
              disabled={rebuilding}
              activeOpacity={0.85}
            >
              <Text style={s.rebuildText}>
                {rebuilding ? 'Rebuilding...' : 'Rebuild Image'}
              </Text>
            </TouchableOpacity>
          </View>

          {/* Add extra tool/dependency */}
          <View style={s.addRow}>
            <TextInput
              value={newPkg}
              onChangeText={setNewPkg}
              placeholder="Add dependency or MCP tool (e.g. numpy)..."
              placeholderTextColor={T.textDim}
              style={s.addInput}
              autoCapitalize="none"
            />
            <TouchableOpacity
              style={s.addBtn}
              onPress={() => addPackageToImage(newPkg)}
              activeOpacity={0.8}
            >
              <Text style={s.addBtnText}>+ Add</Text>
            </TouchableOpacity>
          </View>

          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.extraScroll}>
            {EXTRA_IMAGE_PACKAGES.map(pkg => (
              <TouchableOpacity
                key={pkg}
                style={s.extraChip}
                onPress={() => addPackageToImage(pkg)}
              >
                <Text style={s.extraChipText}>+ {pkg}</Text>
              </TouchableOpacity>
            ))}
          </ScrollView>

          {buildLogs.length > 0 && (
            <View style={s.dockerLogBox}>
              {buildLogs.map((line, i) => (
                <Text
                  key={i}
                  style={[s.dockerLogLine, line.includes('[OK]') && { color: T.green }]}
                >
                  {line}
                </Text>
              ))}
            </View>
          )}
        </View>
      </ScrollView>

      {/* Pop-up AI Dialog when Gmail is stopped */}
      <Modal
        visible={Boolean(aiAlertMsg && aiAlertMsg.includes('Gmail'))}
        transparent
        animationType="fade"
        onRequestClose={() => setAiAlertMsg(null)}
      >
        <View style={s.modalBackdrop}>
          <View style={s.aiModalCard}>
            <Text style={s.aiModalTag}>MCP GATEWAY INTERCEPT</Text>
            <Text style={s.aiModalTitle}>Gmail access not available by MCP agent</Text>
            <Text style={s.aiModalBody}>
              You revoked permission for the <Text style={{ color: T.red, fontWeight: '700' }}>gmail</Text> server. Any agent request to read or draft Gmail will now be blocked until you tap Resume.
            </Text>
            <TouchableOpacity
              style={s.aiModalBtn}
              onPress={() => setAiAlertMsg(null)}
              activeOpacity={0.85}
            >
              <Text style={s.aiModalBtnText}>Got it</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  )
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: T.bg },
  header: { paddingHorizontal: 16, paddingTop: 6, paddingBottom: 10 },
  heading: { color: T.text, fontSize: 22, fontWeight: '700', letterSpacing: -0.4 },
  sub: { color: T.textDim, fontSize: 12, marginTop: 2 },

  filterStripWrap: { marginBottom: 8 },
  filterScroll: { paddingHorizontal: 16, gap: 8 },
  filterChip: {
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 10,
  },
  filterChipActive: { backgroundColor: T.indigoDim, borderColor: T.indigo },
  filterText: { color: T.textSub, fontSize: 12, fontWeight: '600' },
  filterTextActive: { color: T.indigo },

  aiBanner: {
    marginHorizontal: 16, marginBottom: 10,
    backgroundColor: T.redDim, borderWidth: 1, borderColor: T.red + '66',
    borderRadius: 12, padding: 12, flexDirection: 'row', alignItems: 'flex-start', gap: 10,
  },
  aiBannerTitle: { color: T.red, fontSize: 10, fontWeight: '800', letterSpacing: 0.7 },
  aiBannerText: { color: T.text, fontSize: 12, marginTop: 2, lineHeight: 17 },
  aiDismiss: { paddingHorizontal: 6, paddingVertical: 2 },
  aiDismissText: { color: T.textSub, fontSize: 14, fontWeight: '700' },

  content: { paddingHorizontal: 16, paddingBottom: 42 },
  sectionLabel: {
    color: T.textDim, fontSize: 10, fontWeight: '700', letterSpacing: 1,
    marginBottom: 8, marginTop: 4,
  },

  ribbonScroll: { gap: 8, paddingBottom: 14 },
  ribbonPill: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
    paddingHorizontal: 11, paddingVertical: 7, borderRadius: 999,
  },
  ribbonActive: { borderColor: T.green, backgroundColor: T.greenDim },
  ribbonStopped: { borderColor: T.red, backgroundColor: T.redDim },
  ribbonText: { color: T.textSub, fontSize: 11, fontFamily: T.mono, fontWeight: '600' },

  cardsList: { gap: 10, marginBottom: 18 },
  toolCard: {
    backgroundColor: T.surface, borderRadius: 16,
    borderWidth: 1, borderColor: T.border, padding: 13, gap: 7,
  },
  toolCardActive: {
    borderColor: T.green, borderWidth: 1.5, backgroundColor: T.greenDim + '66',
  },
  toolCardStopped: {
    borderColor: T.red, borderWidth: 1.5, backgroundColor: T.redDim + '88',
  },
  toolTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  toolTitleWrap: { flexDirection: 'row', alignItems: 'center', gap: 6, flex: 1, flexWrap: 'wrap' },
  toolName: { color: T.text, fontSize: 14, fontWeight: '700', fontFamily: T.mono },
  catBadge: {
    color: T.textDim, fontSize: 10, fontFamily: T.mono,
    backgroundColor: T.elevated, paddingHorizontal: 6, paddingVertical: 2, borderRadius: 5,
  },
  verText: { color: T.textDim, fontSize: 10, fontFamily: T.mono },

  inUseBadge: {
    backgroundColor: T.greenDim, borderWidth: 1, borderColor: T.green + '55',
    paddingHorizontal: 7, paddingVertical: 2, borderRadius: 6,
  },
  inUseText: { color: T.green, fontSize: 9, fontWeight: '800', letterSpacing: 0.6 },
  stoppedBadge: {
    backgroundColor: T.redDim, borderWidth: 1, borderColor: T.red + '66',
    paddingHorizontal: 7, paddingVertical: 2, borderRadius: 6,
  },
  stoppedText: { color: T.red, fontSize: 9, fontWeight: '800', letterSpacing: 0.6 },

  toolDesc: { color: T.textSub, fontSize: 12 },
  controlsRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 2 },
  permBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center',
    backgroundColor: T.elevated, borderWidth: 1, borderColor: T.border,
    borderRadius: 10, paddingHorizontal: 10, paddingVertical: 7,
  },
  permLabel: { color: T.textDim, fontSize: 11 },
  permVal: { fontSize: 11, fontWeight: '700' },

  stopBtn: {
    backgroundColor: T.redDim, borderWidth: 1, borderColor: T.red + '55',
    paddingHorizontal: 14, paddingVertical: 7, borderRadius: 10,
  },
  resumeBtn: {
    backgroundColor: T.greenDim, borderColor: T.green + '55',
  },
  stopBtnText: { color: T.red, fontSize: 11, fontWeight: '700' },
  resumeBtnText: { color: T.green },

  imageCard: {
    backgroundColor: T.surface, borderRadius: 18,
    borderWidth: 1, borderColor: T.borderMid, padding: 14, gap: 12,
  },
  imageHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10 },
  imageTitle: { color: T.text, fontSize: 13, fontWeight: '700', fontFamily: T.mono },
  imageSha: { color: T.textDim, fontSize: 11, fontFamily: T.mono, marginTop: 2 },
  rebuildBtn: {
    backgroundColor: T.indigo, paddingHorizontal: 12, paddingVertical: 9, borderRadius: 10,
  },
  rebuildText: { color: '#141413', fontSize: 12, fontWeight: '700' },

  addRow: { flexDirection: 'row', gap: 8 },
  addInput: {
    flex: 1, backgroundColor: T.elevated, borderWidth: 1, borderColor: T.border,
    borderRadius: 10, paddingHorizontal: 12, paddingVertical: 8, color: T.text, fontSize: 12,
  },
  addBtn: {
    backgroundColor: T.elevated, borderWidth: 1, borderColor: T.indigo + '66',
    borderRadius: 10, paddingHorizontal: 12, justifyContent: 'center',
  },
  addBtnText: { color: T.indigo, fontSize: 12, fontWeight: '700' },

  extraScroll: { gap: 7 },
  extraChip: {
    backgroundColor: T.elevated, borderWidth: 1, borderColor: T.border,
    borderRadius: 8, paddingHorizontal: 10, paddingVertical: 5,
  },
  extraChipText: { color: T.textSub, fontSize: 11, fontFamily: T.mono },

  dockerLogBox: {
    backgroundColor: '#0E0E0D', borderRadius: 10, borderWidth: 1, borderColor: T.border,
    padding: 10, gap: 4,
  },
  dockerLogLine: { color: T.textSub, fontSize: 11, fontFamily: T.mono },

  modalBackdrop: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.72)',
    justifyContent: 'center', padding: 24,
  },
  aiModalCard: {
    backgroundColor: T.surface, borderRadius: 20, borderWidth: 1.5, borderColor: T.red,
    padding: 20, gap: 10,
  },
  aiModalTag: { color: T.red, fontSize: 10, fontWeight: '800', letterSpacing: 1 },
  aiModalTitle: { color: T.text, fontSize: 17, fontWeight: '700' },
  aiModalBody: { color: T.textSub, fontSize: 13, lineHeight: 19 },
  aiModalBtn: {
    marginTop: 6, backgroundColor: T.indigo, borderRadius: 12,
    paddingVertical: 11, alignItems: 'center',
  },
  aiModalBtnText: { color: '#141413', fontSize: 13, fontWeight: '700' },
})
