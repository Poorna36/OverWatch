export type SessionState =
  | 'CREATED'
  | 'PLANNING'
  | 'STARTING'
  | 'ACTIVE'
  | 'PAUSED'
  | 'ENDING'
  | 'ENDED'
  | 'FAILED'

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

export interface AuditEntry {
  seq: number
  timestamp: Date
  tool: string
  decision: Decision
  reason?: string
  riskTier: RiskTier
  sessionId: string
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

// ─── Seed data ────────────────────────────────────────────────────────────────

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
      {
        id: 'tc_1', seq: 1, timestamp: ago(200), tool: 'files.write',
        args: { path: '/workspace/fetch_title.py', content: '...' },
        decision: 'allow', riskTier: 'safe', reversible: true, durationMs: 48,
      },
      {
        id: 'tc_2', seq: 2, timestamp: ago(190), tool: 'terminal.run',
        args: { cmd: 'pip install requests' },
        decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 3200,
      },
      {
        id: 'tc_3', seq: 3, timestamp: ago(150), tool: 'terminal.run',
        args: { cmd: 'python fetch_title.py https://example.com' },
        decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 820,
      },
      {
        id: 'tc_4', seq: 4, timestamp: ago(90), tool: 'files.write',
        args: { path: '/etc/passwd', content: 'evil' },
        decision: 'block', riskTier: 'critical', reversible: false,
        reason: 'PATH_JAIL — target outside /workspace',
      },
      {
        id: 'tc_5', seq: 5, timestamp: ago(60), tool: 'files.upload',
        args: { path: '/workspace/fetch_title.py', destination: 'drive' },
        decision: 'ask', riskTier: 'sensitive', reversible: false,
        reason: 'Sensitive upload — awaiting approval',
      },
      {
        id: 'tc_6', seq: 6, timestamp: ago(20), tool: 'terminal.run',
        args: { cmd: 'curl https://evil.example' },
        decision: 'block', riskTier: 'critical', reversible: false,
        reason: 'EGRESS_DENIED — not in allowlist',
      },
    ],
    budget: { used: 38, total: 100, costUsd: 0.047, maxCostUsd: 0.5 },
    bandwidthTier: 'video',
    pendingApprovals: [
      {
        id: 'apr_1',
        tool: 'files.upload',
        args: { path: '/workspace/fetch_title.py', destination: 'drive' },
        riskTier: 'sensitive',
        reversible: false,
        expiresAt: future(90),
        createdAt: ago(30),
      },
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
      {
        id: 'tc_a', seq: 1, timestamp: ago(7100), tool: 'browser.open',
        args: { url: 'https://arxiv.org' },
        decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 920,
      },
      {
        id: 'tc_b', seq: 2, timestamp: ago(7000), tool: 'browser.read_page',
        args: { url: 'https://arxiv.org/abs/2309.00267' },
        decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 440,
      },
      {
        id: 'tc_c', seq: 3, timestamp: ago(6800), tool: 'files.write',
        args: { path: '/workspace/report.md' },
        decision: 'allow', riskTier: 'safe', reversible: true, durationMs: 62,
      },
    ],
    budget: { used: 24, total: 100, costUsd: 0.031, maxCostUsd: 0.5 },
    bandwidthTier: 'screenshots',
    pendingApprovals: [],
  },
  {
    id: 'sess_ghi789',
    title: 'Set up TypeScript monorepo with ESLint and Prettier',
    profile: 'coding',
    state: 'PAUSED',
    createdAt: ago(1800),
    startedAt: ago(1780),
    toolCalls: [
      {
        id: 'tc_x', seq: 1, timestamp: ago(1700), tool: 'terminal.run',
        args: { cmd: 'npm init -y' },
        decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 240,
      },
      {
        id: 'tc_y', seq: 2, timestamp: ago(1600), tool: 'files.write',
        args: { path: '/workspace/package.json' },
        decision: 'allow', riskTier: 'safe', reversible: true, durationMs: 38,
      },
    ],
    budget: { used: 12, total: 100, costUsd: 0.014, maxCostUsd: 0.5 },
    bandwidthTier: 'text',
    pendingApprovals: [],
  },
]

// ─── Demo script ──────────────────────────────────────────────────────────────

export const DEMO_EVENTS: Array<{
  delay: number
  event: Omit<ToolCallEvent, 'id' | 'seq'>
}> = [
  {
    delay: 1500,
    event: {
      timestamp: new Date(), tool: 'files.write',
      args: { path: '/workspace/fetch_title.py' },
      decision: 'allow', riskTier: 'safe', reversible: true, durationMs: 48,
    },
  },
  {
    delay: 3200,
    event: {
      timestamp: new Date(), tool: 'terminal.run',
      args: { cmd: 'pip install requests' },
      decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 3100,
    },
  },
  {
    delay: 2800,
    event: {
      timestamp: new Date(), tool: 'terminal.run',
      args: { cmd: 'python fetch_title.py https://example.com' },
      decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 820,
    },
  },
  {
    delay: 1600,
    event: {
      timestamp: new Date(), tool: 'files.write',
      args: { path: '/etc/passwd', content: 'malicious' },
      decision: 'block', riskTier: 'critical', reversible: false,
      reason: 'PATH_JAIL — target outside /workspace',
    },
  },
  {
    delay: 2000,
    event: {
      timestamp: new Date(), tool: 'files.upload',
      args: { path: '/workspace/fetch_title.py', destination: 'drive' },
      decision: 'ask', riskTier: 'sensitive', reversible: false,
      reason: 'Sensitive upload — awaiting approval',
    },
  },
  {
    delay: 4000,
    event: {
      timestamp: new Date(), tool: 'terminal.run',
      args: { cmd: 'curl https://evil.example' },
      decision: 'block', riskTier: 'critical', reversible: false,
      reason: 'EGRESS_DENIED — not in allowlist',
    },
  },
]

export const PROFILES = [
  { name: 'coding', role: 'coder', tools: ['terminal', 'files', 'desktop'] },
  { name: 'general', role: 'assistant', tools: ['files', 'browser', 'desktop'] },
  { name: 'daily_routine', role: 'routine', tools: ['calendar', 'gmail'] },
]
