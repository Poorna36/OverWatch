import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  StatusBar, Animated, Modal, Dimensions,
  FlatList,
} from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import {
  MOCK_SESSIONS, DEMO_EVENTS,
  type Session, type ToolCallEvent, type ApprovalRequest,
} from '../lib/mock-data'
import { T, StateChip, DecisionPill, RiskTag, BudgetStrip, PulseDot } from '../components/ui'

const { width: SW, height: SH } = Dimensions.get('window')

function ft(d: Date) {
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

// ─── VM window frames ─────────────────────────────────────────────────────────
const VM_FRAMES: Array<{ title: string; type: 'terminal' | 'editor' | 'browser'; content: string }> = [
  {
    title: 'bash — /workspace',
    type: 'terminal',
    content: `$ pip install requests\nCollecting requests (2.31.0)\n  Downloading... 62kB\nInstalling: certifi, urllib3, requests\n[OK] Successfully installed`,
  },
  {
    title: 'fetch_title.py — editor',
    type: 'editor',
    content: `import requests\nfrom bs4 import BeautifulSoup\nimport sys\n\ndef fetch_title(url: str) -> str:\n    resp = requests.get(url, timeout=10)\n    soup = BeautifulSoup(resp.text, 'html.parser')\n    return soup.title.string.strip()\n\nif __name__ == '__main__':\n    print(fetch_title(sys.argv[1]))`,
  },
  {
    title: 'bash — /workspace',
    type: 'terminal',
    content: `$ python fetch_title.py https://example.com\nTitle: Example Domain\n\n$ python fetch_title.py https://python.org\nTitle: Welcome to Python.org\n[OK] Done`,
  },
]

// ─── Main session screen ──────────────────────────────────────────────────────
interface SessionScreenProps { navigation: any; route: any }

export function SessionScreen({ navigation, route }: SessionScreenProps) {
  const { sessionId } = route.params ?? { sessionId: 'sess_abc123' }
  const seed = MOCK_SESSIONS.find(s => s.id === sessionId) ?? MOCK_SESSIONS[0]
  const [session, setSession] = useState<Session>({ ...seed, toolCalls: [...seed.toolCalls] })

  const [vmExpanded, setVmExpanded] = useState(true)
  const [mcpOpen, setMcpOpen] = useState(false)
  const [demoRunning, setDemoRunning] = useState(false)
  const [vmFrame, setVmFrame] = useState(0)
  const [vmTyped, setVmTyped] = useState(0)
  const [showApproval, setShowApproval] = useState(false)
  const feedRef = useRef<FlatList>(null)
  const mcpAnim = useRef(new Animated.Value(0)).current

  // VM typing animation
  useEffect(() => {
    if (!vmExpanded) return
    const frame = VM_FRAMES[vmFrame]
    if (vmTyped >= frame.content.length) return
    const id = setTimeout(() => setVmTyped(t => t + 2), 18)
    return () => clearTimeout(id)
  }, [vmTyped, vmFrame, vmExpanded])

  useEffect(() => { setVmTyped(0) }, [vmFrame])

  useEffect(() => {
    if (session.pendingApprovals.length > 0) setShowApproval(true)
  }, [session.pendingApprovals.length])

  // MCP panel animation
  function toggleMcp() {
    if (mcpOpen) {
      Animated.spring(mcpAnim, { toValue: 0, useNativeDriver: true, tension: 80, friction: 12 }).start()
      setMcpOpen(false)
    } else {
      setMcpOpen(true)
      Animated.spring(mcpAnim, { toValue: 1, useNativeDriver: true, tension: 80, friction: 12 }).start()
    }
  }

  async function runDemo() {
    setDemoRunning(true)
    setSession(s => ({ ...s, toolCalls: [], state: 'STARTING', pendingApprovals: [] }))
    await new Promise(r => setTimeout(r, 800))
    setSession(s => ({ ...s, state: 'ACTIVE' }))

    let seq = 1
    for (const { delay, event } of DEMO_EVENTS) {
      await new Promise(r => setTimeout(r, delay))
      const newCall: ToolCallEvent = { ...event, id: `d${seq}`, seq, timestamp: new Date() }
      setVmFrame(f => (f + 1) % VM_FRAMES.length)
      setSession(s => ({
        ...s,
        toolCalls: [...s.toolCalls, newCall],
        budget: { ...s.budget, used: Math.min(s.budget.used + 7, s.budget.total) },
        pendingApprovals: event.decision === 'ask'
          ? [...s.pendingApprovals, {
            id: `a${seq}`, tool: event.tool, args: event.args,
            riskTier: event.riskTier, reversible: event.reversible,
            createdAt: new Date(), expiresAt: new Date(Date.now() + 120_000),
          } as ApprovalRequest]
          : s.pendingApprovals,
      }))
      seq++
      setTimeout(() => feedRef.current?.scrollToEnd?.({ animated: true }), 100)
    }
    setDemoRunning(false)
  }

  const handleApprove = useCallback((aid: string) => {
    setSession(s => ({ ...s, pendingApprovals: s.pendingApprovals.filter(a => a.id !== aid) }))
    setShowApproval(false)
  }, [])

  const handleDeny = useCallback((aid: string) => {
    setSession(s => ({ ...s, pendingApprovals: s.pendingApprovals.filter(a => a.id !== aid) }))
    setShowApproval(false)
  }, [])

  const approval = session.pendingApprovals[0]
  const isEnded = session.state === 'ENDED' || session.state === 'FAILED'
  const mcpTranslate = mcpAnim.interpolate({ inputRange: [0, 1], outputRange: [SW, 0] })

  return (
    <SafeAreaView style={ss.safe} edges={['top', 'left', 'right']}>
      <StatusBar barStyle="light-content" />

      {/* ── Top bar ── */}
      <View style={ss.topBar}>
        <TouchableOpacity onPress={() => navigation.goBack()} style={ss.backBtn} hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}>
          <Text style={ss.backArrow}>←</Text>
        </TouchableOpacity>

        <StateChip state={session.state} />

        <Text style={ss.profileTag}>{session.profile}</Text>

        <View style={{ flex: 1 }} />

        {/* Demo trigger */}
        {!isEnded && (
          <TouchableOpacity
            style={[ss.demoTrigger, demoRunning && ss.demoTriggerActive]}
            onPress={runDemo} disabled={demoRunning} activeOpacity={0.8}
          >
            {demoRunning
              ? <PulseDot color={T.indigo} size={8} />
              : <Text style={ss.demoTriggerIcon}>▶</Text>
            }
            <Text style={ss.demoTriggerText}>{demoRunning ? 'Running' : 'Demo'}</Text>
          </TouchableOpacity>
        )}

        {/* MCP panel toggle */}
        <TouchableOpacity style={[ss.mcpBtn, mcpOpen && ss.mcpBtnActive]} onPress={toggleMcp} activeOpacity={0.8}>
          <Text style={ss.mcpBtnText}>MCP</Text>
        </TouchableOpacity>
      </View>

      {/* ── Task title ── */}
      <View style={ss.taskBanner}>
        <View style={ss.taskBubble}>
          <Text style={ss.taskText} numberOfLines={2}>{session.title}</Text>
        </View>
      </View>

      <View style={{ flex: 1 }}>
        <FlatList
          ref={feedRef}
          data={session.toolCalls}
          keyExtractor={item => item.id}
          style={{ flex: 1 }}
          contentContainerStyle={ss.feedContent}
          ListHeaderComponent={
            <>
              {/* ── VM Window ── */}
              <VMWindow
                frame={VM_FRAMES[vmFrame]}
                typed={vmTyped}
                active={session.state === 'ACTIVE'}
                expanded={vmExpanded}
                onToggle={() => setVmExpanded(v => !v)}
                lastTool={session.toolCalls[session.toolCalls.length - 1]}
              />

              {/* ── Stats row ── */}
              <View style={ss.statsRow}>
                <StatBubble value={session.toolCalls.length} label="calls" />
                <StatBubble
                  value={session.toolCalls.filter(t => t.decision === 'allow').length}
                  label="allowed" color={T.green}
                />
                <StatBubble
                  value={session.toolCalls.filter(t => t.decision === 'block').length}
                  label="blocked" color={T.red}
                />
                <View style={ss.statBudget}>
                  <BudgetStrip
                    used={session.budget.used}
                    total={session.budget.total}
                    costUsd={session.budget.costUsd}
                  />
                </View>
              </View>

              {/* ── Pending approval banner ── */}
              {approval && (
                <TouchableOpacity style={ss.approvalBanner} onPress={() => setShowApproval(true)} activeOpacity={0.85}>
                  <View style={ss.approvalBannerLeft}>
                    <Text style={ss.approvalIcon}>⚑</Text>
                    <View>
                      <Text style={ss.approvalBannerTitle}>Approval required</Text>
                      <Text style={ss.approvalBannerSub}>{approval.tool} · {approval.riskTier}</Text>
                    </View>
                  </View>
                  <Text style={ss.approvalBannerCta}>Review →</Text>
                </TouchableOpacity>
              )}

              {/* ── Controls ── */}
              {!isEnded && (
                <View style={ss.controls}>
                  <TouchableOpacity
                    style={ss.ctrlBtn}
                    activeOpacity={0.8}
                    onPress={() => setSession(s => ({ ...s, state: s.state === 'ACTIVE' ? 'PAUSED' : 'ACTIVE' }))}
                  >
                    <Text style={ss.ctrlBtnText}>{session.state === 'PAUSED' ? '▶ Resume' : '⏸ Pause'}</Text>
                  </TouchableOpacity>
                  <TouchableOpacity style={ss.ctrlBtn} activeOpacity={0.8}>
                    <Text style={ss.ctrlBtnText}>⌨ Take over</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[ss.ctrlBtn, ss.endBtn]} activeOpacity={0.8}
                    onPress={() => {
                      setSession(s => ({ ...s, state: 'ENDED', endedAt: new Date() }))
                      setTimeout(() => navigation.goBack(), 1800)
                    }}
                  >
                    <Text style={[ss.ctrlBtnText, { color: T.red }]}>■ End</Text>
                  </TouchableOpacity>
                </View>
              )}

              {/* Feed label */}
              {session.toolCalls.length > 0 && (
                <Text style={ss.feedLabel}>Gateway log</Text>
              )}

              {session.toolCalls.length === 0 && !demoRunning && (
                <View style={ss.emptyFeed}>
                  <Text style={ss.emptyFeedText}>Waiting for agent activity…</Text>
                  <Text style={ss.emptyFeedSub}>Hit Demo to replay the full scenario</Text>
                </View>
              )}
            </>
          }
          renderItem={({ item, index }) => (
            <ToolCallCard event={item} isLatest={index === session.toolCalls.length - 1} />
          )}
          ListFooterComponent={
            isEnded ? (
              <View style={ss.endedCard}>
                <Text style={ss.endedTitle}>Workspace destroyed</Text>
                <Text style={ss.endedSub}>Container removed · credentials revoked · audit chain sealed</Text>
                <TouchableOpacity onPress={() => navigation.navigate('TasksTab')} style={{ marginTop: 10 }}>
                  <Text style={ss.endedLink}>View tasks & audit →</Text>
                </TouchableOpacity>
              </View>
            ) : undefined
          }
        />
      </View>

      {/* ── MCP Gateway panel ── */}
      {mcpOpen && (
        <TouchableOpacity style={ss.mcpOverlay} activeOpacity={1} onPress={toggleMcp} />
      )}
      <Animated.View style={[ss.mcpPanel, { transform: [{ translateX: mcpTranslate }] }]}>
        <MCPPanel session={session} onClose={toggleMcp} />
      </Animated.View>

      {/* ── Approval modal ── */}
      {approval && (
        <Modal visible={showApproval} transparent animationType="slide">
          <ApprovalSheet
            approval={approval}
            onApprove={() => handleApprove(approval.id)}
            onDeny={() => handleDeny(approval.id)}
          />
        </Modal>
      )}
    </SafeAreaView>
  )
}

// ─── VM Window component ───────────────────────────────────────────────────────
function VMWindow({ frame, typed, active, expanded, onToggle, lastTool }: {
  frame: typeof VM_FRAMES[0]
  typed: number
  active: boolean
  expanded: boolean
  onToggle: () => void
  lastTool?: ToolCallEvent
}) {
  const content = frame.content.slice(0, typed)
  const editorColors: Record<string, string> = {
    'import': T.violet,
    'def': T.indigo,
    'return': T.indigo,
    'if': T.indigo,
    '__name__': T.amber,
  }

  return (
    <View style={vm.wrap}>
      {/* Chrome bar */}
      <View style={vm.chrome}>
        <View style={vm.trafficLights}>
          <View style={[vm.tl, { backgroundColor: '#ff5f57' }]} />
          <View style={[vm.tl, { backgroundColor: '#febc2e' }]} />
          <View style={[vm.tl, { backgroundColor: '#28c840' }]} />
        </View>

        <View style={vm.titleWrap}>
          <Text style={vm.titleDot}>{frame.type === 'terminal' ? '$_' : frame.type === 'editor' ? '</>' : 'web'}</Text>
          <Text style={vm.title} numberOfLines={1}>{frame.title}</Text>
        </View>

        <View style={vm.chromeRight}>
          {active && (
            <View style={vm.liveChip}>
              <PulseDot color={T.green} size={5} />
              <Text style={vm.liveText}>LIVE</Text>
            </View>
          )}
          <TouchableOpacity onPress={onToggle} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Text style={vm.collapseBtn}>{expanded ? '⌃' : '⌄'}</Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* Content */}
      {expanded && (
        <View style={vm.content}>
          {/* Path/status bar */}
          <View style={vm.pathBar}>
            <Text style={vm.pathText}>
              {lastTool ? `executing · ${lastTool.tool}` : '/workspace'}
            </Text>
            <View style={vm.pathRight}>
              <Text style={vm.pathBadge}>{frame.type}</Text>
            </View>
          </View>

          {/* Code area */}
          <View style={vm.codeArea}>
            {/* Line numbers */}
            <View style={vm.lineNums}>
              {content.split('\n').map((_, i) => (
                <Text key={i} style={vm.lineNum}>{i + 1}</Text>
              ))}
            </View>
            {/* Code */}
            <ScrollView style={vm.codeScroll} horizontal showsHorizontalScrollIndicator={false}>
              <View>
                {content.split('\n').map((line, i) => (
                  <VMLine key={i} line={line} type={frame.type} isLast={i === content.split('\n').length - 1 && typed < frame.content.length} />
                ))}
              </View>
            </ScrollView>
          </View>

          {/* Status bar */}
          <View style={vm.statusBar}>
            <Text style={vm.statusText}>
              {frame.type === 'terminal' ? 'bash' : frame.type === 'editor' ? 'Python' : 'Chromium'}
            </Text>
            <Text style={vm.statusDivider}>|</Text>
            <Text style={vm.statusText}>UTF-8</Text>
            <View style={{ flex: 1 }} />
            <Text style={[vm.statusText, { color: active ? T.green : T.textDim }]}>
              {active ? '● running' : '○ idle'}
            </Text>
          </View>
        </View>
      )}
    </View>
  )
}

function VMLine({ line, type, isLast }: { line: string; type: string; isLast: boolean }) {
  const isCmd = type === 'terminal' && line.startsWith('$')
  const isSuccess = line.includes('✓') || line.includes('Successfully')
  const isError = line.includes('error') || line.includes('Error') || line.includes('✕')

  let color = T.textSub
  if (isCmd) color = T.text
  if (isSuccess) color = T.green
  if (isError) color = T.red
  if (type === 'editor') {
    if (line.trim().startsWith('def ') || line.trim().startsWith('import ') || line.trim().startsWith('from '))
      color = T.violet
    else if (line.trim().startsWith('#'))
      color = T.textDim
    else if (line.includes('return') || line.includes('if '))
      color = T.indigo
  }

  return (
    <View style={vm.codeLine}>
      <Text style={[vm.code, { color }]}>{line}</Text>
      {isLast && <Text style={vm.cursor}>▌</Text>}
    </View>
  )
}

// ─── Tool call card ───────────────────────────────────────────────────────────
function ToolCallCard({ event: ev, isLatest }: { event: ToolCallEvent; isLatest: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const borderColor = { allow: T.green, ask: T.amber, block: T.red }[ev.decision]
  const serverIcon: Record<string, string> = {
    files: '◫', terminal: '▪', browser: '◎', desktop: '□', calendar: '▦', gmail: '◈',
  }
  const [server] = ev.tool.split('.')

  return (
    <TouchableOpacity
      style={[tc.card, isLatest && tc.cardLatest]}
      onPress={() => setExpanded(e => !e)}
      activeOpacity={0.8}
    >
      {/* Left accent */}
      <View style={[tc.accent, { backgroundColor: borderColor + '80' }]} />

      <View style={tc.body}>
        {/* Top row */}
        <View style={tc.top}>
          <Text style={tc.serverIcon}>{serverIcon[server] ?? '◈'}</Text>
          <Text style={tc.tool}>{ev.tool}</Text>
          <DecisionPill decision={ev.decision} />
          <Text style={tc.time}>{ft(ev.timestamp)}</Text>
        </View>

        {/* Args preview */}
        <Text style={tc.args} numberOfLines={expanded ? 10 : 1}>
          {Object.entries(ev.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join('  ')}
        </Text>

        {/* Expanded */}
        {expanded && (
          <View style={tc.detail}>
            <View style={tc.detailBox}>
              <Text style={tc.detailCode}>
                {JSON.stringify(ev.args, null, 2)}
              </Text>
            </View>
            <View style={tc.detailMeta}>
              <RiskTag tier={ev.riskTier} />
              <Text style={tc.detailProp}>
                {ev.reversible ? 'reversible' : '! irreversible'}
              </Text>
              {ev.durationMs && (
                <Text style={tc.detailProp}>
                  {ev.durationMs > 1000 ? `${(ev.durationMs / 1000).toFixed(1)}s` : `${ev.durationMs}ms`}
                </Text>
              )}
            </View>
            {ev.reason && <Text style={tc.reason}>{ev.reason}</Text>}
          </View>
        )}
      </View>
    </TouchableOpacity>
  )
}

// ─── MCP Gateway panel ────────────────────────────────────────────────────────
function MCPPanel({ session, onClose }: { session: Session; onClose: () => void }) {
  const servers = [
    { id: 'terminal', label: 'terminal', icon: '▪', active: session.profile === 'coding' || session.profile === 'general' },
    { id: 'files', label: 'files', icon: '◫', active: true },
    { id: 'browser', label: 'browser', icon: '◎', active: session.profile === 'general' },
    { id: 'desktop', label: 'desktop', icon: '□', active: true },
    { id: 'calendar', label: 'calendar', icon: '▦', active: session.profile === 'daily_routine' },
    { id: 'gmail', label: 'gmail', icon: 'M', active: session.profile === 'daily_routine' },
  ]

  const pipelineSteps: Array<{ step: string; label: string; status: 'pass' | 'skip' | 'wait' | 'fail'; isLast?: boolean }> = [
    { step: '01', label: 'Session active?', status: 'pass' },
    { step: '02', label: 'Tool in profile?', status: 'pass' },
    { step: '03', label: 'Within plan?', status: 'skip' },
    { step: '04', label: 'Validate args', status: 'pass' },
    { step: '05', label: 'Policy tier', status: 'pass' },
    { step: '06', label: 'Budget check', status: 'pass' },
    { step: '07', label: 'User approval?', status: session.pendingApprovals.length > 0 ? 'wait' : 'pass' },
    { step: '08', label: 'Execute + audit', status: 'pass' },
  ]

  const stats = {
    allow: session.toolCalls.filter(t => t.decision === 'allow').length,
    ask: session.toolCalls.filter(t => t.decision === 'ask').length,
    block: session.toolCalls.filter(t => t.decision === 'block').length,
  }

  return (
    <View style={mp.panel}>
      {/* Handle */}
      <View style={mp.handle} />

      {/* Header */}
      <View style={mp.header}>
        <View style={mp.headerLeft}>
          <Text style={mp.headerTitle}>MCP Gateway</Text>
          <View style={mp.headerStatus}>
            <PulseDot color={T.green} size={6} />
            <Text style={mp.headerStatusText}>firewall active</Text>
          </View>
        </View>
        <TouchableOpacity onPress={onClose} style={mp.closeBtn} hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}>
          <Text style={mp.closeBtnText}>✕</Text>
        </TouchableOpacity>
      </View>

      <ScrollView style={mp.scroll} showsVerticalScrollIndicator={false}>

        {/* Decision summary */}
        <View style={mp.section}>
          <Text style={mp.sectionLabel}>Decisions</Text>
          <View style={mp.decisionRow}>
            <DecisionStat label="Allowed" value={stats.allow} color={T.green} />
            <DecisionStat label="Ask" value={stats.ask} color={T.amber} />
            <DecisionStat label="Blocked" value={stats.block} color={T.red} />
          </View>
        </View>

        {/* Gateway pipeline */}
        <View style={mp.section}>
          <Text style={mp.sectionLabel}>Pipeline</Text>
          <View style={mp.pipeline}>
            {pipelineSteps.map((step, i) => (
              <PipelineStep key={step.step} {...step} isLast={i === pipelineSteps.length - 1} />
            ))}
          </View>
        </View>

        {/* MCP servers */}
        <View style={mp.section}>
          <Text style={mp.sectionLabel}>MCP Servers</Text>
          <View style={mp.servers}>
            {servers.map(sv => (
              <View key={sv.id} style={[mp.server, !sv.active && mp.serverInactive]}>
                <Text style={[mp.serverIcon, !sv.active && { color: T.textFaint }]}>{sv.icon}</Text>
                <Text style={[mp.serverLabel, !sv.active && { color: T.textFaint }]}>{sv.label}</Text>
                <View style={[mp.serverDot, { backgroundColor: sv.active ? T.green : T.textFaint }]} />
              </View>
            ))}
          </View>
        </View>

        {/* Recent gateway log */}
        <View style={mp.section}>
          <Text style={mp.sectionLabel}>Recent calls</Text>
          {session.toolCalls.slice(-5).reverse().map(ev => (
            <View key={ev.id} style={mp.logRow}>
              <View style={[mp.logDot, {
                backgroundColor: { allow: T.green, ask: T.amber, block: T.red }[ev.decision],
              }]} />
              <Text style={mp.logTool} numberOfLines={1}>{ev.tool}</Text>
              <Text style={[mp.logDecision, {
                color: { allow: T.green, ask: T.amber, block: T.red }[ev.decision],
              }]}>{ev.decision}</Text>
              {ev.durationMs && (
                <Text style={mp.logMs}>{ev.durationMs}ms</Text>
              )}
            </View>
          ))}
          {session.toolCalls.length === 0 && (
            <Text style={mp.logEmpty}>No calls yet</Text>
          )}
        </View>

        <View style={{ height: 40 }} />
      </ScrollView>
    </View>
  )
}

function DecisionStat({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <View style={mp.decisionStat}>
      <Text style={[mp.decisionValue, { color }]}>{value}</Text>
      <Text style={mp.decisionLabel}>{label}</Text>
    </View>
  )
}

function PipelineStep({ step, label, status, isLast }: {
  step: string; label: string; status: 'pass' | 'skip' | 'wait' | 'fail'; isLast: boolean
}) {
  const cfg = {
    pass: { color: T.green, icon: '✓', dim: false },
    skip: { color: T.textDim, icon: '—', dim: true },
    wait: { color: T.amber, icon: '⟳', dim: false },
    fail: { color: T.red, icon: '✕', dim: false },
  }[status]

  return (
    <View style={mp.pipelineStep}>
      {/* Connector line */}
      {!isLast && <View style={[mp.pipelineConnector, { opacity: cfg.dim ? 0.2 : 0.4 }]} />}
      {/* Step */}
      <View style={[mp.pipelineIcon, {
        borderColor: cfg.color + '44',
        backgroundColor: cfg.color + '11',
        opacity: cfg.dim ? 0.4 : 1,
      }]}>
        <Text style={[mp.pipelineIconText, { color: cfg.color }]}>{cfg.icon}</Text>
      </View>
      <View style={[{ opacity: cfg.dim ? 0.4 : 1 }]}>
        <Text style={mp.pipelineStep_num}>{step}</Text>
        <Text style={mp.pipelineStep_label}>{label}</Text>
      </View>
    </View>
  )
}

// ─── Approval bottom sheet ────────────────────────────────────────────────────
function ApprovalSheet({ approval, onApprove, onDeny }: {
  approval: ApprovalRequest; onApprove: () => void; onDeny: () => void
}) {
  const [secs, setSecs] = useState(
    Math.max(0, Math.floor((approval.expiresAt.getTime() - Date.now()) / 1000))
  )
  const progress = useRef(new Animated.Value(1)).current

  useEffect(() => {
    const total = Math.floor((approval.expiresAt.getTime() - approval.createdAt.getTime()) / 1000)
    Animated.timing(progress, {
      toValue: 0, duration: secs * 1000, useNativeDriver: false,
    }).start()
    const id = setInterval(() => {
      setSecs(s => { if (s <= 1) { onDeny(); return 0 } return s - 1 })
    }, 1000)
    return () => clearInterval(id)
  }, [])

  const barWidth = progress.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] })
  const isUrgent = secs < 30

  return (
    <View style={ap.overlay}>
      <View style={ap.sheet}>
        <View style={ap.handle} />

        {/* Progress bar */}
        <View style={ap.progressTrack}>
          <Animated.View style={[ap.progressFill, {
            width: barWidth as any,
            backgroundColor: isUrgent ? T.amber : T.indigo,
          }]} />
        </View>

        {/* Header */}
        <View style={ap.header}>
          <View>
            <Text style={ap.eyebrow}>Approval Required</Text>
            <Text style={ap.tool}>{approval.tool}</Text>
          </View>
          <View style={[ap.countdown, isUrgent && ap.countdownUrgent]}>
            <Text style={[ap.countdownNum, isUrgent && { color: T.amber }]}>{secs}</Text>
            <Text style={ap.countdownUnit}>sec</Text>
          </View>
        </View>

        {/* Args */}
        <View style={ap.argsBox}>
          {Object.entries(approval.args).map(([k, v]) => (
            <View key={k} style={ap.argRow}>
              <Text style={ap.argKey}>{k}</Text>
              <Text style={ap.argVal} numberOfLines={1}>{JSON.stringify(v)}</Text>
            </View>
          ))}
        </View>

        {/* Risk */}
        <View style={ap.riskRow}>
          <View style={[ap.riskChip, { borderColor: approval.riskTier === 'critical' ? T.red + '50' : T.amber + '50' }]}>
            <Text style={[ap.riskText, { color: approval.riskTier === 'critical' ? T.red : T.amber }]}>
              {approval.riskTier}
            </Text>
          </View>
          {!approval.reversible && (
            <Text style={ap.irreversible}>⚠ Cannot be undone</Text>
          )}
        </View>

        {/* Actions */}
        <View style={ap.actions}>
          <TouchableOpacity style={ap.denyBtn} onPress={onDeny} activeOpacity={0.8}>
            <Text style={ap.denyText}>Deny</Text>
          </TouchableOpacity>
          <TouchableOpacity style={ap.approveBtn} onPress={onApprove} activeOpacity={0.85}>
            <Text style={ap.approveText}>Approve</Text>
          </TouchableOpacity>
        </View>
      </View>
    </View>
  )
}

// ─── Stat bubble ──────────────────────────────────────────────────────────────
function StatBubble({ value, label, color = T.textSub }: { value: number; label: string; color?: string }) {
  return (
    <View style={ss.statBubble}>
      <Text style={[ss.statNum, { color }]}>{value}</Text>
      <Text style={ss.statLabel}>{label}</Text>
    </View>
  )
}

// ─── Styles ───────────────────────────────────────────────────────────────────
const ss = StyleSheet.create({
  safe: { flex: 1, backgroundColor: T.bg },

  topBar: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    paddingHorizontal: 16, paddingVertical: 10,
    borderBottomWidth: 1, borderBottomColor: T.border,
  },
  backBtn: {},
  backArrow: { color: T.textSub, fontSize: 22 },
  profileTag: { color: T.textDim, fontSize: 11, fontFamily: T.mono },
  demoTrigger: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    backgroundColor: T.indigoDim, borderWidth: 1, borderColor: T.indigo + '40',
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10,
  },
  demoTriggerActive: { borderColor: T.indigo + '80', backgroundColor: T.indigo + '20' },
  demoTriggerIcon: { color: T.indigo, fontSize: 11 },
  demoTriggerText: { color: T.indigo, fontSize: 12, fontWeight: '700' },
  mcpBtn: {
    backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10,
  },
  mcpBtnActive: { borderColor: T.violet + '60', backgroundColor: T.violetDim },
  mcpBtnText: { color: T.textSub, fontSize: 11, fontWeight: '800', letterSpacing: 1 },

  taskBanner: {
    paddingHorizontal: 16, paddingVertical: 10,
    borderBottomWidth: 1, borderBottomColor: T.border,
    alignItems: 'flex-end',
  },
  taskBubble: {
    backgroundColor: T.indigoDim, borderRadius: 16, borderTopRightRadius: 4,
    borderWidth: 1, borderColor: T.indigo + '30',
    paddingHorizontal: 14, paddingVertical: 10,
    maxWidth: '90%',
  },
  taskText: { color: T.text, fontSize: 13, lineHeight: 19 },

  feedContent: { padding: 14, gap: 6, paddingBottom: 40 },

  statsRow: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    marginVertical: 6,
  },
  statBubble: { alignItems: 'center' },
  statNum: { fontSize: 20, fontWeight: '800', fontFamily: T.mono, lineHeight: 24 },
  statLabel: { fontSize: 9, color: T.textDim, letterSpacing: 0.5 },
  statBudget: { flex: 1 },

  approvalBanner: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    backgroundColor: T.amberDim, borderWidth: 1, borderColor: T.amber + '40',
    borderRadius: 14, padding: 14, marginVertical: 4,
  },
  approvalBannerLeft: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  approvalIcon: { color: T.amber, fontSize: 20 },
  approvalBannerTitle: { color: T.amber, fontSize: 14, fontWeight: '700' },
  approvalBannerSub: { color: T.amber + '80', fontSize: 11, fontFamily: T.mono, marginTop: 1 },
  approvalBannerCta: { color: T.amber, fontSize: 13, fontWeight: '700' },

  controls: { flexDirection: 'row', gap: 8, marginVertical: 6 },
  ctrlBtn: {
    flex: 1, backgroundColor: T.surface, borderWidth: 1, borderColor: T.border,
    borderRadius: 12, paddingVertical: 10, alignItems: 'center',
  },
  endBtn: { borderColor: T.red + '40', backgroundColor: T.redDim },
  ctrlBtnText: { color: T.textSub, fontSize: 12, fontWeight: '600' },

  feedLabel: {
    color: T.textDim, fontSize: 10, fontWeight: '700', letterSpacing: 1.8,
    textTransform: 'uppercase', marginTop: 8, marginBottom: 4,
  },
  emptyFeed: { paddingVertical: 32, alignItems: 'center', gap: 6 },
  emptyFeedText: { color: T.textDim, fontSize: 14 },
  emptyFeedSub: { color: T.textFaint, fontSize: 12 },

  endedCard: {
    backgroundColor: T.surface, borderRadius: 18, borderWidth: 1,
    borderColor: T.border, padding: 20, alignItems: 'center',
    marginTop: 10,
  },
  endedTitle: { color: T.text, fontSize: 16, fontWeight: '700', marginBottom: 4 },
  endedSub: { color: T.textSub, fontSize: 12, textAlign: 'center', lineHeight: 18 },
  endedLink: { color: T.indigo, fontSize: 13 },

  mcpOverlay: { ...StyleSheet.absoluteFill, backgroundColor: 'rgba(0,0,0,0.5)', zIndex: 10 },
  mcpPanel: {
    position: 'absolute', top: 0, right: 0, bottom: 0,
    width: SW * 0.82, backgroundColor: T.surface,
    borderLeftWidth: 1, borderLeftColor: T.border,
    zIndex: 20,
  },
})

const vm = StyleSheet.create({
  wrap: {
    backgroundColor: '#0d0d10', borderRadius: 16,
    borderWidth: 1, borderColor: T.border,
    overflow: 'hidden', marginBottom: 6,
  },
  chrome: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    paddingHorizontal: 12, paddingVertical: 9,
    backgroundColor: '#141418', borderBottomWidth: 1, borderBottomColor: T.border,
  },
  trafficLights: { flexDirection: 'row', gap: 5 },
  tl: { width: 11, height: 11, borderRadius: 6 },
  titleWrap: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 5 },
  titleDot: { color: T.textDim, fontSize: 12, fontFamily: T.mono },
  title: { color: T.textSub, fontSize: 11, fontFamily: T.mono, flex: 1 },
  chromeRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  liveChip: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: T.greenDim, borderRadius: 999, paddingHorizontal: 7, paddingVertical: 3,
    borderWidth: 1, borderColor: T.green + '30',
  },
  liveText: { color: T.green, fontSize: 9, fontWeight: '800', letterSpacing: 1 },
  collapseBtn: { color: T.textDim, fontSize: 14 },

  content: { backgroundColor: '#0a0a0d' },
  pathBar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 10, paddingVertical: 5,
    borderBottomWidth: 1, borderBottomColor: T.border + '44',
  },
  pathText: { color: T.textDim, fontSize: 10, fontFamily: T.mono },
  pathRight: {},
  pathBadge: { color: T.textFaint, fontSize: 9, fontFamily: T.mono },

  codeArea: { flexDirection: 'row', minHeight: 120, maxHeight: 180 },
  lineNums: {
    paddingHorizontal: 8, paddingVertical: 10, alignItems: 'flex-end',
    borderRightWidth: 1, borderRightColor: T.border + '44',
  },
  lineNum: { color: T.textFaint, fontSize: 10, fontFamily: T.mono, lineHeight: 17 },
  codeScroll: { flex: 1 },
  codeLine: { flexDirection: 'row', paddingHorizontal: 10, alignItems: 'center' },
  code: { fontSize: 11, fontFamily: T.mono, lineHeight: 17 },
  cursor: { color: T.indigo, fontSize: 11, opacity: 0.9 },

  statusBar: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 10, paddingVertical: 5,
    borderTopWidth: 1, borderTopColor: T.border + '44',
    backgroundColor: '#0d0d10',
  },
  statusText: { color: T.textFaint, fontSize: 9, fontFamily: T.mono },
  statusDivider: { color: T.textFaint + '44', fontSize: 9 },
})

const tc = StyleSheet.create({
  card: {
    flexDirection: 'row', backgroundColor: T.surface,
    borderRadius: 12, overflow: 'hidden',
    borderWidth: 1, borderColor: T.border,
  },
  cardLatest: { borderColor: T.borderMid },
  accent: { width: 3 },
  body: { flex: 1, padding: 10, gap: 4 },
  top: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  serverIcon: { color: T.textSub, fontSize: 12, fontFamily: T.mono },
  tool: { flex: 1, color: T.text, fontSize: 12, fontFamily: T.mono, fontWeight: '600' },
  time: { color: T.textFaint, fontSize: 9, fontFamily: T.mono },
  args: { color: T.textDim, fontSize: 10, fontFamily: T.mono },
  detail: { gap: 6, marginTop: 4 },
  detailBox: {
    backgroundColor: T.bg, borderRadius: 8, padding: 8,
    borderWidth: 1, borderColor: T.border,
  },
  detailCode: { color: T.textSub, fontSize: 10, fontFamily: T.mono, lineHeight: 16 },
  detailMeta: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  detailProp: { color: T.textDim, fontSize: 10, fontFamily: T.mono },
  reason: { color: T.amber + 'cc', fontSize: 10, fontFamily: T.mono },
})

const mp = StyleSheet.create({
  panel: { flex: 1 },
  handle: {
    width: 32, height: 3, backgroundColor: T.border,
    borderRadius: 2, alignSelf: 'center', marginTop: 10,
  },
  header: {
    flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between',
    paddingHorizontal: 20, paddingTop: 16, paddingBottom: 12,
    borderBottomWidth: 1, borderBottomColor: T.border,
  },
  headerLeft: { gap: 4 },
  headerTitle: { color: T.text, fontSize: 18, fontWeight: '800', letterSpacing: -0.5 },
  headerStatus: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  headerStatusText: { color: T.green, fontSize: 11, fontWeight: '600' },
  closeBtn: {},
  closeBtnText: { color: T.textDim, fontSize: 18 },

  scroll: { flex: 1 },
  section: { paddingHorizontal: 20, paddingTop: 20, paddingBottom: 4 },
  sectionLabel: {
    color: T.textDim, fontSize: 10, fontWeight: '700',
    letterSpacing: 1.8, textTransform: 'uppercase', marginBottom: 12,
  },

  decisionRow: { flexDirection: 'row', gap: 10 },
  decisionStat: {
    flex: 1, backgroundColor: T.elevated, borderRadius: 14,
    padding: 14, alignItems: 'center', borderWidth: 1, borderColor: T.border,
  },
  decisionValue: { fontSize: 28, fontWeight: '800', fontFamily: T.mono },
  decisionLabel: { color: T.textDim, fontSize: 10, marginTop: 2 },

  pipeline: { gap: 0 },
  pipelineStep: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    paddingVertical: 7, position: 'relative',
  },
  pipelineConnector: {
    position: 'absolute', left: 15, top: '50%', width: 1,
    height: '100%', backgroundColor: T.border,
  },
  pipelineIcon: {
    width: 30, height: 30, borderRadius: 8,
    borderWidth: 1, alignItems: 'center', justifyContent: 'center',
  },
  pipelineIconText: { fontSize: 12, fontWeight: '700' },
  pipelineStep_num: { color: T.textFaint, fontSize: 9, fontFamily: T.mono },
  pipelineStep_label: { color: T.textSub, fontSize: 12, fontWeight: '500' },

  servers: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  server: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: T.elevated, borderRadius: 10,
    borderWidth: 1, borderColor: T.border,
    paddingHorizontal: 10, paddingVertical: 7,
  },
  serverInactive: { opacity: 0.4 },
  serverIcon: { color: T.textSub, fontSize: 12 },
  serverLabel: { color: T.textSub, fontSize: 11, fontFamily: T.mono },
  serverDot: { width: 5, height: 5, borderRadius: 3 },

  logRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: T.border + '44',
  },
  logDot: { width: 5, height: 5, borderRadius: 3 },
  logTool: { flex: 1, color: T.textSub, fontSize: 11, fontFamily: T.mono },
  logDecision: { fontSize: 10, fontFamily: T.mono, fontWeight: '700' },
  logMs: { color: T.textFaint, fontSize: 10, fontFamily: T.mono },
  logEmpty: { color: T.textFaint, fontSize: 12, paddingVertical: 12 },
})

const ap = StyleSheet.create({
  overlay: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.75)' },
  sheet: {
    backgroundColor: T.surface, borderTopLeftRadius: 28, borderTopRightRadius: 28,
    paddingHorizontal: 20, paddingBottom: 36,
    borderTopWidth: 1, borderTopColor: T.borderMid,
  },
  handle: {
    width: 32, height: 3, backgroundColor: T.border, borderRadius: 2,
    alignSelf: 'center', marginTop: 10, marginBottom: 4,
  },
  progressTrack: { height: 2, backgroundColor: T.border, borderRadius: 1, marginBottom: 20 },
  progressFill: { height: 2, borderRadius: 1 },
  header: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start',
    marginBottom: 16,
  },
  eyebrow: { color: T.textDim, fontSize: 10, fontWeight: '700', letterSpacing: 1.5, marginBottom: 4 },
  tool: { color: T.text, fontSize: 22, fontWeight: '800', letterSpacing: -0.5 },
  countdown: {
    backgroundColor: T.indigoDim, borderRadius: 14, borderWidth: 1,
    borderColor: T.indigo + '40', padding: 12, alignItems: 'center', minWidth: 56,
  },
  countdownUrgent: { backgroundColor: T.amberDim, borderColor: T.amber + '40' },
  countdownNum: { color: T.indigo, fontSize: 26, fontWeight: '800', fontFamily: T.mono },
  countdownUnit: { color: T.textDim, fontSize: 10 },

  argsBox: {
    backgroundColor: T.bg, borderRadius: 14, padding: 14,
    borderWidth: 1, borderColor: T.border, marginBottom: 14, gap: 8,
  },
  argRow: { flexDirection: 'row', gap: 10 },
  argKey: { color: T.textDim, fontSize: 11, fontFamily: T.mono, minWidth: 80 },
  argVal: { color: T.text, fontSize: 11, fontFamily: T.mono, flex: 1 },

  riskRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 20 },
  riskChip: {
    borderWidth: 1, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 4,
  },
  riskText: { fontSize: 11, fontWeight: '700', fontFamily: T.mono },
  irreversible: { color: T.amber, fontSize: 12 },

  actions: { flexDirection: 'row', gap: 10 },
  denyBtn: {
    flex: 1, borderWidth: 1, borderColor: T.border, borderRadius: 16,
    paddingVertical: 16, alignItems: 'center', backgroundColor: T.elevated,
  },
  denyText: { color: T.textSub, fontSize: 15, fontWeight: '700' },
  approveBtn: {
    flex: 1, backgroundColor: T.indigo, borderRadius: 16,
    paddingVertical: 16, alignItems: 'center',
  },
  approveText: { color: '#fff', fontSize: 15, fontWeight: '800' },
})
