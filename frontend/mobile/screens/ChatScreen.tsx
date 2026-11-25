import React, { useState, useRef } from 'react'
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity, TextInput,
  StatusBar, KeyboardAvoidingView, Platform, Modal,
  Animated, Dimensions, ScrollView,
} from 'react-native'
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context'
import {
  ROLES, SUGGESTED_PROMPTS,
  type AgentRole, type ChatMessage, type ChatThread, type EnvConfig, type ToolCallEvent,
} from '../lib/mock-data'
import { buildSimulation } from '../lib/agent-sim'
import { T, DecisionPill, PulseDot, ProfileAvatar } from '../components/ui'

const { width: SW } = Dimensions.get('window')

interface ChatScreenProps {
  chats: ChatThread[]
  activeChatId: string | null
  onSelectChat: (id: string | null) => void
  onUpdateChats: React.Dispatch<React.SetStateAction<ChatThread[]>>
  env: EnvConfig
  userEmail: string
  navigation: any
}

export function ChatScreen({
  chats, activeChatId, onSelectChat, onUpdateChats, env, userEmail, navigation,
}: ChatScreenProps) {
  const [input, setInput] = useState('')
  const [role, setRole] = useState<AgentRole>('general')
  const [rolePopupOpen, setRolePopupOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [mcpOpen, setMcpOpen] = useState(false)
  const [running, setRunning] = useState(false)
  const [execStatus, setExecStatus] = useState('... Executing task')
  const [livePreview, setLivePreview] = useState<{ title: string; lines: string[] } | null>(null)
  const [liveSteps, setLiveSteps] = useState<ToolCallEvent[]>([])

  const insets = useSafeAreaInsets()
  const listRef = useRef<FlatList>(null)
  const sidebarAnim = useRef(new Animated.Value(0)).current

  const activeChat = chats.find(c => c.id === activeChatId) ?? null
  const currentRole = activeChat ? activeChat.role : role
  const roleObj = ROLES.find(r => r.id === currentRole) ?? ROLES[0]

  function openSidebar() {
    setSidebarOpen(true)
    Animated.spring(sidebarAnim, { toValue: 1, useNativeDriver: true, tension: 85, friction: 12 }).start()
  }

  function closeSidebar() {
    Animated.spring(sidebarAnim, { toValue: 0, useNativeDriver: true, tension: 85, friction: 12 }).start(() => {
      setSidebarOpen(false)
    })
  }

  function handleNewChat() {
    onSelectChat(null)
    setLivePreview(null)
    setLiveSteps([])
    closeSidebar()
  }

  function handleSelectRole(r: AgentRole) {
    setRole(r)
    if (activeChat) {
      onUpdateChats(prev => prev.map(c => c.id === activeChat.id ? { ...c, role: r } : c))
    }
    setRolePopupOpen(false)
  }

  async function sendPrompt(rawText?: string) {
    const prompt = (rawText ?? input).trim()
    if (!prompt || running) return

    setInput('')
    setRunning(true)
    setLiveSteps([])
    setLivePreview(null)
    setExecStatus(`... Executing task on ${env.mode === 'desktop' ? 'Remote Desktop' : 'VM'}`)

    const sim = buildSimulation(prompt, currentRole, env)
    const userMsg: ChatMessage = {
      id: `u_${Date.now()}`,
      sender: 'user',
      text: prompt,
      timestamp: new Date(),
    }

    let threadId = activeChat?.id
    if (!threadId) {
      threadId = `chat_${Date.now()}`
      const newThread: ChatThread = {
        id: threadId,
        title: sim.title,
        role: currentRole,
        updatedAt: new Date(),
        messages: [userMsg],
      }
      onUpdateChats(prev => [newThread, ...prev])
      onSelectChat(threadId)
    } else {
      onUpdateChats(prev =>
        prev.map(c =>
          c.id === threadId
            ? { ...c, updatedAt: new Date(), messages: [...c.messages, userMsg] }
            : c
        )
      )
    }

    setTimeout(() => listRef.current?.scrollToEnd?.({ animated: true }), 80)

    const collectedCalls: ToolCallEvent[] = []
    let lastPreview: { title: string; lines: string[] } | undefined

    for (let i = 0; i < sim.steps.length; i++) {
      const step = sim.steps[i]
      setExecStatus(step.statusLabel || '... Executing task')
      setTimeout(() => listRef.current?.scrollToEnd?.({ animated: true }), 80)
      await new Promise(r => setTimeout(r, step.delayMs))

      if (step.previewTitle && step.previewLines) {
        lastPreview = { title: step.previewTitle, lines: step.previewLines }
        setLivePreview(lastPreview)
      }

      if (step.toolCall) {
        const tc: ToolCallEvent = {
          ...step.toolCall,
          id: `tc_${Date.now()}_${i}`,
          seq: i + 1,
          timestamp: new Date(),
        }
        collectedCalls.push(tc)
        setLiveSteps([...collectedCalls])
      }
      setTimeout(() => listRef.current?.scrollToEnd?.({ animated: true }), 80)
    }

    const agentMsg: ChatMessage = {
      id: `a_${Date.now()}`,
      sender: 'agent',
      role: currentRole,
      text: sim.finalText,
      timestamp: new Date(),
      toolCalls: collectedCalls,
      previewTitle: lastPreview?.title,
      previewLines: lastPreview?.lines,
      approval: sim.approval
        ? {
          ...sim.approval,
          id: `apr_${Date.now()}`,
          createdAt: new Date(),
          expiresAt: new Date(Date.now() + 120_000),
        }
        : undefined,
      approvalStatus: sim.approval ? 'pending' : undefined,
    }

    onUpdateChats(prev =>
      prev.map(c =>
        c.id === threadId
          ? { ...c, updatedAt: new Date(), messages: [...c.messages, agentMsg] }
          : c
      )
    )

    setLiveSteps([])
    setRunning(false)
    setTimeout(() => listRef.current?.scrollToEnd?.({ animated: true }), 100)
  }

  function handleMessageApproval(msgId: string, decision: 'approved' | 'denied') {
    if (!activeChat) return
    onUpdateChats(prev =>
      prev.map(c =>
        c.id !== activeChat.id
          ? c
          : {
            ...c,
            messages: c.messages.map(m =>
              m.id === msgId
                ? {
                  ...m,
                  approvalStatus: decision,
                  text:
                    decision === 'approved'
                      ? m.text + '\n\n✓ **Approved & submitted** via MCP gateway.'
                      : m.text + '\n\n✕ **Denied** — submission cancelled.',
                }
                : m
            ),
          }
      )
    )
  }

  const sidebarTranslate = sidebarAnim.interpolate({
    inputRange: [0, 1],
    outputRange: [-SW * 0.8, 0],
  })

  return (
    <SafeAreaView style={s.safe} edges={['top', 'left', 'right']}>
      <StatusBar barStyle="light-content" />

      {/* ── Top Header ── */}
      <View style={s.header}>
        <TouchableOpacity onPress={openSidebar} style={s.iconBtn} activeOpacity={0.75}>
          <Text style={s.iconBtnText}>☰</Text>
        </TouchableOpacity>

        {/* ChatGPT-style Role Selector Pill */}
        <TouchableOpacity
          style={s.rolePill}
          onPress={() => setRolePopupOpen(true)}
          activeOpacity={0.8}
        >
          <Text style={s.rolePillText}>{roleObj.label}</Text>
          <Text style={s.roleChevron}>▾</Text>
        </TouchableOpacity>

        <View style={{ flex: 1 }} />

        {/* Environment badge */}
        <TouchableOpacity
          style={s.envPill}
          onPress={() => navigation.navigate('SettingsTab')}
          activeOpacity={0.8}
        >
          <PulseDot color={env.mode === 'desktop' ? T.green : T.indigo} size={6} />
          <Text style={s.envPillText}>
            {env.mode === 'desktop' ? 'Desktop' : 'VM'}
          </Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={[s.mcpBtn, mcpOpen && s.mcpBtnActive]}
          onPress={() => setMcpOpen(o => !o)}
          activeOpacity={0.8}
        >
          <Text style={[s.mcpBtnText, mcpOpen && { color: T.indigo }]}>MCP</Text>
        </TouchableOpacity>
      </View>

      {/* ── Main KeyboardAvoidingView wrapping messages + composer ── */}
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 10 : 24}
      >
        {/* Conversation or Empty State with Suggestions ABOVE */}
        {!activeChat || activeChat.messages.length === 0 ? (
          <ScrollView
            style={{ flex: 1 }}
            contentContainerStyle={s.emptyScroll}
            keyboardShouldPersistTaps="handled"
          >
            <View style={s.heroWrap}>
              <View style={s.heroMark}>
                <Text style={s.heroGlyph}>OW</Text>
              </View>
              <Text style={s.heroTitle}>What can I help with?</Text>
              <Text style={s.heroSub}>
                Connected to {env.mode === 'desktop' ? 'Remote Desktop' : `VM (${env.vmImage})`} · {roleObj.label}
              </Text>
            </View>

            {/* Suggestions UP above the chat input */}
            <View style={s.suggestionsBox}>
              <Text style={s.suggestHeader}>SUGGESTIONS</Text>
              {SUGGESTED_PROMPTS.map((item, idx) => (
                <TouchableOpacity
                  key={idx}
                  style={s.suggestCard}
                  onPress={() => {
                    setRole(item.role)
                    sendPrompt(item.prompt)
                  }}
                  activeOpacity={0.8}
                >
                  <View style={s.suggestTop}>
                    <Text style={s.suggestLabel}>{item.label}</Text>
                    <Text style={s.suggestRoleTag}>
                      {item.role === 'general' ? 'General' : item.role === 'code' ? 'Code' : 'Research'}
                    </Text>
                  </View>
                  <Text style={s.suggestPrompt} numberOfLines={2}>{item.prompt}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </ScrollView>
        ) : (
          <FlatList
            ref={listRef}
            data={activeChat.messages}
            keyExtractor={m => m.id}
            style={{ flex: 1 }}
            contentContainerStyle={s.msgList}
            keyboardShouldPersistTaps="handled"
            renderItem={({ item }) => (
              <MessageBubble
                msg={item}
                onApprove={() => handleMessageApproval(item.id, 'approved')}
                onDeny={() => handleMessageApproval(item.id, 'denied')}
              />
            )}
            ListFooterComponent={
              running ? (
                <View style={s.streamingBox}>
                  <View style={s.streamingHead}>
                    <PulseDot color={T.indigo} size={7} />
                    <Text style={s.streamingTitle}>{execStatus}</Text>
                  </View>
                  <Text style={s.execBanner}>
                    Working in background on {env.mode === 'desktop' ? 'Remote Desktop' : 'VM'} (3-5s sync)...
                  </Text>
                  {liveSteps.map(tc => (
                    <View key={tc.id} style={s.stepRow}>
                      <Text style={s.stepTool}>{tc.tool}</Text>
                      <DecisionPill decision={tc.decision} />
                    </View>
                  ))}
                  {livePreview && (
                    <View style={s.vmCard}>
                      <View style={s.vmBar}>
                        <View style={[s.dot, { backgroundColor: T.red }]} />
                        <View style={[s.dot, { backgroundColor: T.amber }]} />
                        <View style={[s.dot, { backgroundColor: T.green }]} />
                        <Text style={s.vmTitle}>{livePreview.title}</Text>
                      </View>
                      {livePreview.lines.map((line, i) => (
                        <Text key={i} style={s.vmLine}>{line}</Text>
                      ))}
                    </View>
                  )}
                </View>
              ) : undefined
            }
          />
        )}

        {/* ── Composer ALWAYS below, lifted above keyboard ── */}
        <View style={s.composerWrap}>
          <View style={s.composerBox}>
            <TextInput
              value={input}
              onChangeText={setInput}
              placeholder={`Message ${roleObj.label}…`}
              placeholderTextColor={T.textDim}
              style={s.composerInput}
              multiline
              maxLength={1000}
            />
            <TouchableOpacity
              style={[s.sendBtn, (!input.trim() || running) && s.sendBtnDisabled]}
              onPress={() => sendPrompt()}
              disabled={!input.trim() || running}
              activeOpacity={0.8}
            >
              <Text style={s.sendIcon}>{running ? '…' : '↑'}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </KeyboardAvoidingView>

      {/* ── Role Selector Popup (ChatGPT style) ── */}
      <Modal visible={rolePopupOpen} transparent animationType="fade" onRequestClose={() => setRolePopupOpen(false)}>
        <TouchableOpacity style={s.modalOverlay} activeOpacity={1} onPress={() => setRolePopupOpen(false)}>
          <View style={s.rolePopup}>
            <Text style={s.popupHeading}>Select Role</Text>
            {ROLES.map(r => {
              const selected = r.id === currentRole
              return (
                <TouchableOpacity
                  key={r.id}
                  style={[s.roleOption, selected && s.roleOptionActive]}
                  onPress={() => handleSelectRole(r.id)}
                  activeOpacity={0.8}
                >
                  <View style={{ flex: 1 }}>
                    <Text style={[s.roleOptName, selected && { color: T.indigo }]}>{r.label}</Text>
                    <Text style={s.roleOptDesc}>{r.desc}</Text>
                    <View style={s.roleToolsRow}>
                      {r.tools.map(t => (
                        <Text key={t} style={s.roleToolTag}>{t}</Text>
                      ))}
                    </View>
                  </View>
                  {selected && <Text style={s.roleCheck}>✓</Text>}
                </TouchableOpacity>
              )
            })}
          </View>
        </TouchableOpacity>
      </Modal>

      {/* ── MCP Gateway Side Drawer ── */}
      <Modal visible={mcpOpen} transparent animationType="fade" onRequestClose={() => setMcpOpen(false)}>
        <TouchableOpacity style={s.mcpOverlay} activeOpacity={1} onPress={() => setMcpOpen(false)}>
          <View style={[s.mcpDrawer, { paddingTop: Math.max(insets.top + 14, 28) }]} onStartShouldSetResponder={() => true}>
            <View style={s.mcpHeader}>
              <Text style={s.mcpTitle}>MCP Gateway</Text>
              <TouchableOpacity onPress={() => setMcpOpen(false)}>
                <Text style={s.mcpClose}>✕</Text>
              </TouchableOpacity>
            </View>
            <Text style={s.mcpSub}>
              {env.mode === 'desktop' ? `Bridge: ${env.desktopHost}` : `VM: ${env.vmImage}`}
            </Text>
            <Text style={s.mcpSection}>ACTIVE MCP AGENTS</Text>
            {(env.mode === 'vm' ? env.vmMcpAgents : ['filesystem', 'terminal', 'browser', 'calendar']).map(a => (
              <View key={a} style={s.mcpRow}>
                <PulseDot color={T.green} size={6} />
                <Text style={s.mcpName}>{a}</Text>
                <Text style={s.mcpState}>ready</Text>
              </View>
            ))}
            <Text style={s.mcpSection}>POLICY PIPELINE</Text>
            {['01 Session active', '02 Role allowlist', '03 Arg validation', '04 Risk tier check', '05 Audit hash'].map(step => (
              <View key={step} style={s.mcpPipeRow}>
                <Text style={s.mcpPipeText}>{step}</Text>
                <Text style={s.mcpPipePass}>pass</Text>
              </View>
            ))}
          </View>
        </TouchableOpacity>
      </Modal>

      {/* ── Left Sidebar Drawer (Previous Chats) ── */}
      {sidebarOpen && (
        <View style={s.sidebarBackdrop}>
          <TouchableOpacity style={StyleSheet.absoluteFill} activeOpacity={1} onPress={closeSidebar} />
          <Animated.View style={[s.sidebar, { paddingTop: Math.max(insets.top + 14, 24), transform: [{ translateX: sidebarTranslate }] }]}>
            <View style={s.sidebarTop}>
              <Text style={s.sidebarBrand}>Overwatch</Text>
              <TouchableOpacity onPress={closeSidebar}>
                <Text style={s.sidebarClose}>✕</Text>
              </TouchableOpacity>
            </View>

            <TouchableOpacity style={s.newChatBtn} onPress={handleNewChat} activeOpacity={0.85}>
              <Text style={s.newChatText}>+  New Chat</Text>
            </TouchableOpacity>

            <Text style={s.sidebarLabel}>RECENT CHATS</Text>
            <ScrollView style={{ flex: 1 }} showsVerticalScrollIndicator={false}>
              {chats.map(c => {
                const isSel = c.id === activeChat?.id
                return (
                  <TouchableOpacity
                    key={c.id}
                    style={[s.chatItem, isSel && s.chatItemActive]}
                    onPress={() => {
                      onSelectChat(c.id)
                      setLivePreview(null)
                      closeSidebar()
                    }}
                    activeOpacity={0.75}
                  >
                    <Text style={[s.chatItemTitle, isSel && { color: T.text }]} numberOfLines={1}>
                      {c.title}
                    </Text>
                    <Text style={s.chatItemMeta}>
                      {c.role === 'general' ? 'General' : c.role === 'code' ? 'Code' : 'Research'} · {c.messages.length} msgs
                    </Text>
                  </TouchableOpacity>
                )
              })}
            </ScrollView>

            <View style={s.sidebarFooter}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                <ProfileAvatar size={36} online />
                <View style={{ flex: 1 }}>
                  <View style={s.sidebarEnvRow}>
                    <PulseDot color={env.mode === 'desktop' ? T.green : T.indigo} size={6} />
                    <Text style={s.sidebarEnvText} numberOfLines={1}>
                      {env.mode === 'desktop' ? 'Remote Desktop' : `VM · ${env.vmImage}`}
                    </Text>
                  </View>
                  <Text style={s.sidebarUser} numberOfLines={1}>{userEmail}</Text>
                </View>
              </View>
            </View>
          </Animated.View>
        </View>
      )}
    </SafeAreaView>
  )
}

function MessageBubble({
  msg, onApprove, onDeny,
}: { msg: ChatMessage; onApprove: () => void; onDeny: () => void }) {
  if (msg.sender === 'user') {
    return (
      <View style={s.userRow}>
        <View style={s.userBubble}>
          <Text style={s.userText}>{msg.text}</Text>
        </View>
      </View>
    )
  }

  return (
    <View style={s.agentBlock}>
      {/* Tool calls summary */}
      {msg.toolCalls && msg.toolCalls.length > 0 && (
        <View style={s.toolStrip}>
          {msg.toolCalls.map(tc => (
            <View key={tc.id} style={s.toolChip}>
              <Text style={s.toolChipName}>{tc.tool}</Text>
              <Text style={[
                s.toolChipState,
                { color: tc.decision === 'allow' ? T.green : tc.decision === 'ask' ? T.amber : T.red },
              ]}>
                {tc.decision}
              </Text>
            </View>
          ))}
        </View>
      )}

      {/* Inline Desktop/VM preview snapshot */}
      {msg.previewTitle && msg.previewLines && (
        <View style={s.vmCard}>
          <View style={s.vmBar}>
            <View style={[s.dot, { backgroundColor: T.red }]} />
            <View style={[s.dot, { backgroundColor: T.amber }]} />
            <View style={[s.dot, { backgroundColor: T.green }]} />
            <Text style={s.vmTitle}>{msg.previewTitle}</Text>
          </View>
          {msg.previewLines.map((line, i) => (
            <Text key={i} style={s.vmLine}>{line}</Text>
          ))}
        </View>
      )}

      <Text style={s.agentText}>{msg.text}</Text>

      {/* Inline Approval Request */}
      {msg.approval && msg.approvalStatus === 'pending' && (
        <View style={s.approvalCard}>
          <Text style={s.approvalTitle}>Approval required · {msg.approval.tool}</Text>
          <Text style={s.approvalSub}>{msg.approval.summary}</Text>
          <View style={s.approvalBtns}>
            <TouchableOpacity style={s.denyBtn} onPress={onDeny} activeOpacity={0.8}>
              <Text style={s.denyBtnText}>Deny</Text>
            </TouchableOpacity>
            <TouchableOpacity style={s.approveBtn} onPress={onApprove} activeOpacity={0.8}>
              <Text style={s.approveBtnText}>Approve & Run</Text>
            </TouchableOpacity>
          </View>
        </View>
      )}
    </View>
  )
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: T.bg },

  header: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 14, paddingVertical: 10,
    borderBottomWidth: 1, borderBottomColor: T.border,
    backgroundColor: T.bg,
  },
  iconBtn: {
    width: 36, height: 36, borderRadius: 10,
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
    alignItems: 'center', justifyContent: 'center',
  },
  iconBtnText: { color: T.text, fontSize: 16 },

  rolePill: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 12,
  },
  rolePillText: { color: T.text, fontSize: 13, fontWeight: '600' },
  roleChevron: { color: T.textDim, fontSize: 11 },

  envPill: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999,
  },
  envPillText: { color: T.textSub, fontSize: 11, fontWeight: '600' },

  mcpBtn: {
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10,
  },
  mcpBtnActive: { borderColor: T.indigo, backgroundColor: T.indigoDim },
  mcpBtnText: { color: T.textSub, fontSize: 11, fontWeight: '700', fontFamily: T.mono },

  emptyScroll: { flexGrow: 1, justifyContent: 'space-between', padding: 16, paddingBottom: 12 },
  heroWrap: { alignItems: 'center', marginTop: 28, marginBottom: 24 },
  heroMark: {
    width: 48, height: 48, borderRadius: 14,
    backgroundColor: T.indigoDim, borderWidth: 1, borderColor: T.indigo + '55',
    alignItems: 'center', justifyContent: 'center', marginBottom: 14,
  },
  heroGlyph: { color: T.indigo, fontSize: 22 },
  heroTitle: { color: T.text, fontSize: 22, fontWeight: '700', letterSpacing: -0.4 },
  heroSub: { color: T.textDim, fontSize: 12, marginTop: 4 },

  suggestionsBox: { gap: 8, marginTop: 'auto' as any },
  suggestHeader: { color: T.textDim, fontSize: 10, fontWeight: '700', letterSpacing: 1, marginBottom: 2 },
  suggestCard: {
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
    borderRadius: 14, padding: 13,
  },
  suggestTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  suggestLabel: { color: T.indigo, fontSize: 12, fontWeight: '700' },
  suggestRoleTag: { color: T.textDim, fontSize: 10, fontFamily: T.mono },
  suggestPrompt: { color: T.textSub, fontSize: 13, lineHeight: 18 },

  msgList: { padding: 16, paddingBottom: 12, gap: 16 },
  userRow: { alignItems: 'flex-end' },
  userBubble: {
    backgroundColor: T.elevated, borderWidth: 1, borderColor: T.border,
    borderRadius: 18, borderBottomRightRadius: 4,
    paddingHorizontal: 14, paddingVertical: 10, maxWidth: '85%',
  },
  userText: { color: T.text, fontSize: 14, lineHeight: 20 },

  agentBlock: { gap: 8 },
  toolStrip: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  toolChip: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
    borderRadius: 8, paddingHorizontal: 8, paddingVertical: 4,
  },
  toolChipName: { color: T.textSub, fontSize: 11, fontFamily: T.mono },
  toolChipState: { fontSize: 10, fontWeight: '700', fontFamily: T.mono },
  agentText: { color: T.text, fontSize: 14, lineHeight: 21 },

  vmCard: {
    backgroundColor: '#0E0E0D', borderRadius: 12,
    borderWidth: 1, borderColor: T.border, padding: 10, gap: 3,
  },
  vmBar: { flexDirection: 'row', alignItems: 'center', gap: 5, marginBottom: 5 },
  dot: { width: 7, height: 7, borderRadius: 4, opacity: 0.75 },
  vmTitle: { color: T.textDim, fontSize: 10, fontFamily: T.mono, marginLeft: 4 },
  vmLine: { color: T.green, fontSize: 11, fontFamily: T.mono, lineHeight: 16 },

  streamingBox: {
    backgroundColor: T.surface, borderRadius: 14,
    borderWidth: 1, borderColor: T.border, padding: 12, gap: 8, marginTop: 6,
  },
  streamingHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  streamingTitle: { color: T.text, fontSize: 13, fontWeight: '600', flex: 1 },
  execBanner: { color: T.indigo, fontSize: 11, fontFamily: T.mono },
  stepRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  stepTool: { color: T.text, fontSize: 12, fontFamily: T.mono },

  approvalCard: {
    backgroundColor: T.amberDim, borderWidth: 1, borderColor: T.amber + '55',
    borderRadius: 14, padding: 12, gap: 6, marginTop: 4,
  },
  approvalTitle: { color: T.amber, fontSize: 12, fontWeight: '700' },
  approvalSub: { color: T.textSub, fontSize: 12 },
  approvalBtns: { flexDirection: 'row', gap: 8, marginTop: 4 },
  denyBtn: {
    flex: 1, paddingVertical: 9, borderRadius: 10,
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border, alignItems: 'center',
  },
  denyBtnText: { color: T.textSub, fontSize: 12, fontWeight: '600' },
  approveBtn: {
    flex: 2, paddingVertical: 9, borderRadius: 10,
    backgroundColor: T.indigo, alignItems: 'center',
  },
  approveBtnText: { color: '#141413', fontSize: 12, fontWeight: '700' },

  composerWrap: {
    paddingHorizontal: 12, paddingTop: 8, paddingBottom: 10,
    borderTopWidth: 1, borderTopColor: T.border, backgroundColor: T.bg,
  },
  composerBox: {
    flexDirection: 'row', alignItems: 'flex-end', gap: 8,
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.borderMid,
    borderRadius: 18, paddingLeft: 14, paddingRight: 8, paddingVertical: 8,
  },
  composerInput: {
    flex: 1, color: T.text, fontSize: 14, maxHeight: 110, paddingVertical: 4,
  },
  sendBtn: {
    width: 34, height: 34, borderRadius: 11,
    backgroundColor: T.indigo, alignItems: 'center', justifyContent: 'center',
  },
  sendBtnDisabled: { backgroundColor: T.indigoDim },
  sendIcon: { color: '#141413', fontSize: 16, fontWeight: '800' },

  modalOverlay: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.65)',
    justifyContent: 'flex-start', paddingTop: 64, paddingHorizontal: 16,
  },
  rolePopup: {
    backgroundColor: T.surface, borderRadius: 18,
    borderWidth: 1, borderColor: T.borderMid, padding: 14, gap: 8,
  },
  popupHeading: { color: T.textDim, fontSize: 11, fontWeight: '700', letterSpacing: 0.8, marginBottom: 4 },
  roleOption: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    backgroundColor: T.elevated, borderRadius: 12, borderWidth: 1, borderColor: T.border,
    padding: 12,
  },
  roleOptionActive: { borderColor: T.indigo, backgroundColor: T.indigoDim + '66' },
  roleOptName: { color: T.text, fontSize: 14, fontWeight: '700' },
  roleOptDesc: { color: T.textSub, fontSize: 12, marginTop: 2 },
  roleToolsRow: { flexDirection: 'row', gap: 6, marginTop: 6 },
  roleToolTag: {
    color: T.textDim, fontSize: 10, fontFamily: T.mono,
    backgroundColor: T.surface, paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4,
  },
  roleCheck: { color: T.indigo, fontSize: 16, fontWeight: '700' },

  mcpOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', flexDirection: 'row', justifyContent: 'flex-end' },
  mcpDrawer: {
    width: SW * 0.78, backgroundColor: T.surface,
    borderLeftWidth: 1, borderLeftColor: T.border, padding: 18, paddingTop: 46,
  },
  mcpHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  mcpTitle: { color: T.text, fontSize: 16, fontWeight: '700' },
  mcpClose: { color: T.textDim, fontSize: 16, padding: 4 },
  mcpSub: { color: T.textDim, fontSize: 11, fontFamily: T.mono, marginBottom: 18 },
  mcpSection: { color: T.textDim, fontSize: 10, fontWeight: '700', letterSpacing: 1, marginTop: 14, marginBottom: 8 },
  mcpRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: T.border,
  },
  mcpName: { color: T.text, fontSize: 13, fontFamily: T.mono, flex: 1 },
  mcpState: { color: T.green, fontSize: 11, fontFamily: T.mono },
  mcpPipeRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 6 },
  mcpPipeText: { color: T.textSub, fontSize: 12 },
  mcpPipePass: { color: T.green, fontSize: 11, fontFamily: T.mono },

  sidebarBackdrop: { ...StyleSheet.absoluteFill, backgroundColor: 'rgba(0,0,0,0.6)', zIndex: 30 },
  sidebar: {
    position: 'absolute', top: 0, bottom: 0, left: 0, width: SW * 0.78,
    backgroundColor: T.surface, borderRightWidth: 1, borderRightColor: T.border,
    padding: 16, paddingTop: 20,
  },
  sidebarTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 },
  sidebarBrand: { color: T.text, fontSize: 18, fontWeight: '700' },
  sidebarClose: { color: T.textDim, fontSize: 16, padding: 4 },
  newChatBtn: {
    backgroundColor: T.indigo, borderRadius: 12,
    paddingVertical: 11, alignItems: 'center', marginBottom: 18,
  },
  newChatText: { color: '#141413', fontSize: 13, fontWeight: '700' },
  sidebarLabel: { color: T.textDim, fontSize: 10, fontWeight: '700', letterSpacing: 1, marginBottom: 8 },
  chatItem: {
    paddingVertical: 10, paddingHorizontal: 10, borderRadius: 10, marginBottom: 4,
  },
  chatItemActive: { backgroundColor: T.elevated, borderWidth: 1, borderColor: T.border },
  chatItemTitle: { color: T.textSub, fontSize: 13, fontWeight: '600' },
  chatItemMeta: { color: T.textDim, fontSize: 11, marginTop: 2 },
  sidebarFooter: {
    borderTopWidth: 1, borderTopColor: T.border, paddingTop: 12, gap: 4,
  },
  sidebarEnvRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  sidebarEnvText: { color: T.textSub, fontSize: 12, fontWeight: '600' },
  sidebarUser: { color: T.textDim, fontSize: 11 },
})
