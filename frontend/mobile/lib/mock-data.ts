// Shared mock data and types for Overwatch mobile app
export type SessionState =
  | 'CREATED' | 'PLANNING' | 'STARTING' | 'ACTIVE'
  | 'PAUSED' | 'ENDING' | 'ENDED' | 'FAILED'

export type Decision = 'allow' | 'ask' | 'block'
export type RiskTier = 'safe' | 'sensitive' | 'critical'
export type BandwidthTier = 'video' | 'screenshots' | 'text'

export interface ToolCallEvent {
  id: string
  seq: number
  timestamp: Date
  tool: string
  args: Record<string, unknown>
  decision: Decision
  reason?: string
  riskTier: RiskTier
  reversible: boolean
  durationMs?: number
}

export interface ApprovalRequest {
  id: string
  tool: string
  args: Record<string, unknown>
  riskTier: RiskTier
  reversible: boolean
  expiresAt: Date
  createdAt: Date
}

export interface Session {
  id: string
  title: string
  profile: 'coding' | 'general' | 'daily_routine'
  state: SessionState
  createdAt: Date
  startedAt?: Date
  endedAt?: Date
  toolCalls: ToolCallEvent[]
  budget: { used: number; total: number; costUsd: number; maxCostUsd: number }
  bandwidthTier: BandwidthTier
  pendingApprovals: ApprovalRequest[]
}

function ago(seconds: number): Date {
  return new Date(Date.now() - seconds * 1000)
}

function future(seconds: number): Date {
  return new Date(Date.now() + seconds * 1000)
}

export const MOCK_SESSIONS: Session[] = [
  {
    id: 'sess_abc123',
    title: 'Create a Python script that fetches a page title and runs it',
    profile: 'coding',
    state: 'ACTIVE',
    createdAt: ago(240),
    startedAt: ago(220),
    toolCalls: [
      { id: 'tc_1', seq: 1, timestamp: ago(200), tool: 'files.write', args: { path: '/workspace/fetch_title.py' }, decision: 'allow', riskTier: 'safe', reversible: true, durationMs: 48 },
      { id: 'tc_2', seq: 2, timestamp: ago(190), tool: 'terminal.run', args: { cmd: 'pip install requests' }, decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 3200 },
      { id: 'tc_3', seq: 3, timestamp: ago(150), tool: 'terminal.run', args: { cmd: 'python fetch_title.py' }, decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 820 },
      { id: 'tc_4', seq: 4, timestamp: ago(90), tool: 'files.write', args: { path: '/etc/passwd' }, decision: 'block', riskTier: 'critical', reversible: false, reason: 'PATH_JAIL' },
      { id: 'tc_5', seq: 5, timestamp: ago(60), tool: 'files.upload', args: { path: '/workspace/fetch_title.py', destination: 'drive' }, decision: 'ask', riskTier: 'sensitive', reversible: false, reason: 'Awaiting approval' },
    ],
    budget: { used: 38, total: 100, costUsd: 0.047, maxCostUsd: 0.5 },
    bandwidthTier: 'video',
    pendingApprovals: [
      { id: 'apr_1', tool: 'files.upload', args: { path: '/workspace/fetch_title.py', destination: 'drive' }, riskTier: 'sensitive', reversible: false, expiresAt: future(90), createdAt: ago(30) },
    ],
  },
  {
    id: 'sess_def456',
    title: 'Research report on LLM safety techniques',
    profile: 'general',
    state: 'ENDED',
    createdAt: ago(7200),
    startedAt: ago(7180),
    endedAt: ago(3600),
    toolCalls: [
      { id: 'tc_a', seq: 1, timestamp: ago(7100), tool: 'browser.open', args: { url: 'https://arxiv.org' }, decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 920 },
      { id: 'tc_b', seq: 2, timestamp: ago(7000), tool: 'browser.read_page', args: { url: 'https://arxiv.org/abs/2309.00267' }, decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 440 },
    ],
    budget: { used: 24, total: 100, costUsd: 0.031, maxCostUsd: 0.5 },
    bandwidthTier: 'screenshots',
    pendingApprovals: [],
  },
]

export const DEMO_EVENTS: Array<{ delay: number; event: Omit<ToolCallEvent, 'id' | 'seq'> }> = [
  { delay: 1500, event: { timestamp: new Date(), tool: 'files.write', args: { path: '/workspace/fetch_title.py' }, decision: 'allow', riskTier: 'safe', reversible: true, durationMs: 48 } },
  { delay: 3000, event: { timestamp: new Date(), tool: 'terminal.run', args: { cmd: 'pip install requests' }, decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 3100 } },
  { delay: 2500, event: { timestamp: new Date(), tool: 'terminal.run', args: { cmd: 'python fetch_title.py' }, decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 820 } },
  { delay: 1800, event: { timestamp: new Date(), tool: 'files.write', args: { path: '/etc/passwd' }, decision: 'block', riskTier: 'critical', reversible: false, reason: 'PATH_JAIL — target outside /workspace' } },
  { delay: 2200, event: { timestamp: new Date(), tool: 'files.upload', args: { path: '/workspace/fetch_title.py', destination: 'drive' }, decision: 'ask', riskTier: 'sensitive', reversible: false, reason: 'Awaiting approval' } },
  { delay: 3500, event: { timestamp: new Date(), tool: 'terminal.run', args: { cmd: 'curl https://evil.example' }, decision: 'block', riskTier: 'critical', reversible: false, reason: 'EGRESS_DENIED — not in allowlist' } },
]
