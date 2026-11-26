// Shared mock data and types for Overwatch mobile app
export type SessionState =
  | 'CREATED' | 'PLANNING' | 'STARTING' | 'ACTIVE'
  | 'PAUSED' | 'ENDING' | 'ENDED' | 'FAILED'

export type Decision = 'allow' | 'ask' | 'block'
export type RiskTier = 'safe' | 'sensitive' | 'critical'
export type BandwidthTier = 'video' | 'screenshots' | 'text'
export type AgentRole = 'general' | 'code' | 'research'
export type EnvMode = 'desktop' | 'vm'
export type PermissionLevel = 'allow_always' | 'ask_every_time' | 'read_only' | 'deny'

export interface ScopeToolItem {
  id: string
  name: string
  category: 'tool' | 'dependency'
  desc: string
  version: string
  inUse: boolean
  stopped: boolean
  permission: PermissionLevel
}

export const INITIAL_SCOPE_TOOLS: ScopeToolItem[] = [
  { id: 'terminal',        name: 'terminal',        category: 'tool',       desc: 'Shell execution & pytest runner',     version: 'bash 5.2',    inUse: true,  stopped: false, permission: 'allow_always' },
  { id: 'filesystem',      name: 'filesystem',      category: 'tool',       desc: 'Scoped /workspace file read & write', version: 'mcp-fs 1.4',  inUse: true,  stopped: false, permission: 'allow_always' },
  { id: 'google-calendar', name: 'google-calendar', category: 'tool',       desc: 'Calendar events & reminders bridge',  version: 'gcal v3',     inUse: true,  stopped: false, permission: 'read_only' },
  { id: 'gmail',           name: 'gmail',           category: 'tool',       desc: 'Inbox read & draft composer',         version: 'gmail v1',    inUse: true,  stopped: false, permission: 'ask_every_time' },
  { id: 'browser',         name: 'browser',         category: 'tool',       desc: 'Chromium DOM fill & navigation',      version: 'cr-131',      inUse: false, stopped: false, permission: 'ask_every_time' },
  { id: 'search-agent',    name: 'search-agent',    category: 'tool',       desc: 'Web & arXiv retrieval agent',         version: 'mcp-2.1',     inUse: false, stopped: false, permission: 'allow_always' },
  { id: 'google-photos',   name: 'google-photos',   category: 'tool',       desc: 'Media scan & album organizer',        version: 'gphotos v1',  inUse: false, stopped: false, permission: 'read_only' },
  { id: 'app-store',       name: 'app-store',       category: 'tool',       desc: 'Package & desktop app catalog',       version: 'store-1.0',   inUse: false, stopped: false, permission: 'ask_every_time' },
  { id: 'python-3.12',     name: 'python-3.12',     category: 'dependency', desc: 'Python runtime & virtual env',        version: '3.12.7',      inUse: false, stopped: false, permission: 'allow_always' },
  { id: 'pytest',          name: 'pytest',          category: 'dependency', desc: 'Automated Python test suite',         version: '8.3.3',       inUse: false, stopped: false, permission: 'allow_always' },
  { id: 'playwright',      name: 'playwright',      category: 'dependency', desc: 'Headless browser automation',         version: '1.49.0',      inUse: false, stopped: false, permission: 'allow_always' },
  { id: 'git',             name: 'git',             category: 'dependency', desc: 'Repo status, diff & branch state',    version: '2.47.1',      inUse: false, stopped: false, permission: 'allow_always' },
  { id: 'docker-cli',      name: 'docker-cli',      category: 'dependency', desc: 'Sandbox container builder',           version: '27.3.1',      inUse: false, stopped: false, permission: 'ask_every_time' },
  { id: 'poppler-pdf',     name: 'poppler-pdf',     category: 'dependency', desc: 'PDF text & table extraction',         version: '24.08',       inUse: false, stopped: false, permission: 'allow_always' },
]

export const EXTRA_IMAGE_PACKAGES = ['ffmpeg', 'pandas', 'redis-cli', 'sqlite3', 'curl', 'ripgrep']

export interface EnvConfig {
  mode: EnvMode
  desktopHost: string
  vmImage: string
  vmDeps: string[]
  vmIncludes: string[]
  vmMcpAgents: string[]
}

export const DEFAULT_ENV: EnvConfig = {
  mode: 'desktop',
  desktopHost: 'poorn-workstation (Win 11 · RDP)',
  vmImage: 'ubuntu-24.04-dev',
  vmDeps: ['Python 3.12', 'Playwright', 'Git + SSH'],
  vmIncludes: ['Workspace /src', 'Local Calendar Sync'],
  vmMcpAgents: ['filesystem', 'terminal', 'browser', 'calendar'],
}

export const VM_IMAGES = [
  { id: 'ubuntu-24.04-dev', label: 'Ubuntu 24.04 Dev', desc: 'Lightweight container · 2 vCPU / 4 GB' },
  { id: 'python-3.12-ml', label: 'Python 3.12 + Data', desc: 'Preloaded pip, venv, pytest, pandas' },
  { id: 'node-22-browser', label: 'Node 22 + Chromium', desc: 'Headless browser + Playwright' },
  { id: 'minimal-alpine', label: 'Minimal Alpine', desc: 'Fast boot · CLI & MCP only' },
]

export const VM_DEPS_OPTIONS = ['Python 3.12', 'Node.js 22', 'Playwright', 'Docker CLI', 'Git + SSH', 'Poppler PDF']
export const VM_INCLUDES_OPTIONS = [
  'Workspace /src',
  'Google Photos',
  'Gmail',
  'Google Calendar',
  'App Store',
  'Search Agent',
  'Browser',
]
export const VM_MCP_OPTIONS = [
  'filesystem',
  'terminal',
  'browser',
  'search-agent',
  'google-calendar',
  'gmail',
  'google-photos',
  'app-store',
]

export const ROLES: Array<{
  id: AgentRole
  label: string
  short: string
  desc: string
  tools: string[]
}> = [
  {
    id: 'general',
    label: 'General Work',
    short: 'General',
    desc: 'Calendar, reminders, forms, browser & everyday tasks',
    tools: ['calendar', 'browser', 'files', 'gmail'],
  },
  {
    id: 'code',
    label: 'Code',
    short: 'Code',
    desc: 'Python/TS projects, terminal, tests & debugging',
    tools: ['files', 'terminal', 'git', 'python'],
  },
  {
    id: 'research',
    label: 'Research',
    short: 'Research',
    desc: 'Deep web search, reading papers & structured synthesis',
    tools: ['browser', 'search', 'notes'],
  },
]

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
  summary?: string
}

export interface ChatMessage {
  id: string
  sender: 'user' | 'agent'
  text: string
  timestamp: Date
  role?: AgentRole
  toolCalls?: ToolCallEvent[]
  approval?: ApprovalRequest
  approvalStatus?: 'pending' | 'approved' | 'denied'
  previewTitle?: string
  previewLines?: string[]
}

export interface ChatThread {
  id: string
  title: string
  role: AgentRole
  updatedAt: Date
  messages: ChatMessage[]
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

export const SUGGESTED_PROMPTS: Array<{ role: AgentRole; label: string; prompt: string }> = [
  {
    role: 'general',
    label: 'Calendar & Deadlines',
    prompt: 'Open my calendar, browse through my reminders and give me deadlines',
  },
  {
    role: 'general',
    label: 'Fill Application Form',
    prompt: 'Go to this specific website and fill an application form for me',
  },
  {
    role: 'code',
    label: 'Resume Python Project',
    prompt: 'Resume building my project in Python',
  },
]

export const MOCK_CHATS: ChatThread[] = [
  {
    id: 'chat_deadlines',
    title: 'Calendar Deadlines & Reminders',
    role: 'general',
    updatedAt: ago(1800),
    messages: [
      {
        id: 'm1',
        sender: 'user',
        text: 'Open my calendar, browse through my reminders and give me deadlines',
        timestamp: ago(1820),
      },
      {
        id: 'm2',
        sender: 'agent',
        role: 'general',
        text: 'Checked your Calendar and Reminders:\n\n• **Today, 4:30 PM** — Project meeting with Shreya (Google Meet)\n• **Today, 7:00 PM** — Submit Overwatch slides to Prof. Nair\n• **Tomorrow, 11:00 AM** — Dentist appointment (Indiranagar)\n• **Sat, Nov 28, 6:00 PM** — Debug auth module with Aarav',
        timestamp: ago(1800),
        toolCalls: [
          { id: 'c1', seq: 1, timestamp: ago(1815), tool: 'calendar.list_events', args: { range: 'next_7_days' }, decision: 'allow', riskTier: 'safe', reversible: true, durationMs: 142 },
          { id: 'c2', seq: 2, timestamp: ago(1810), tool: 'reminders.fetch', args: { list: 'All' }, decision: 'allow', riskTier: 'safe', reversible: true, durationMs: 95 },
        ],
      },
    ],
  },
  {
    id: 'chat_python',
    title: 'Python MCP Client Resume',
    role: 'code',
    updatedAt: ago(7200),
    messages: [
      {
        id: 'm3',
        sender: 'user',
        text: 'Resume building my project in Python',
        timestamp: ago(7250),
      },
      {
        id: 'm4',
        sender: 'agent',
        role: 'code',
        text: 'Picked up from your last commit in `overwatch-agent/`:\n\n• Ran `pytest` — 14 passed, 1 skipped\n• Fixed async timeout in `gateway_client.py`\n• Ready to continue with the policy cache module.',
        timestamp: ago(7200),
        toolCalls: [
          { id: 'c3', seq: 1, timestamp: ago(7240), tool: 'files.scan_workspace', args: { path: '/workspace/overwatch-agent' }, decision: 'allow', riskTier: 'safe', reversible: true, durationMs: 64 },
          { id: 'c4', seq: 2, timestamp: ago(7220), tool: 'terminal.run', args: { cmd: 'pytest -q' }, decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 1120 },
        ],
      },
    ],
  },
  {
    id: 'chat_form',
    title: 'Portal Application Form',
    role: 'general',
    updatedAt: ago(25000),
    messages: [
      {
        id: 'm5',
        sender: 'user',
        text: 'Go to this specific website and fill an application form for me',
        timestamp: ago(25100),
      },
      {
        id: 'm6',
        sender: 'agent',
        role: 'general',
        text: 'Navigated to the application portal and filled out Name, Email, GitHub, and Project Summary. Stopped before clicking **Submit** for your confirmation.',
        timestamp: ago(25000),
        approvalStatus: 'approved',
      },
    ],
  },
]

export const MOCK_SESSIONS: Session[] = [
  {
    id: 'sess_abc123',
    title: 'Resume building my project in Python',
    profile: 'coding',
    state: 'ACTIVE',
    createdAt: ago(240),
    startedAt: ago(220),
    toolCalls: [
      { id: 'tc_1', seq: 1, timestamp: ago(200), tool: 'files.write', args: { path: '/workspace/gateway_client.py' }, decision: 'allow', riskTier: 'safe', reversible: true, durationMs: 48 },
      { id: 'tc_2', seq: 2, timestamp: ago(190), tool: 'terminal.run', args: { cmd: 'pytest -q' }, decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 1420 },
      { id: 'tc_3', seq: 3, timestamp: ago(150), tool: 'terminal.run', args: { cmd: 'python main.py --check' }, decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 620 },
      { id: 'tc_4', seq: 4, timestamp: ago(90), tool: 'files.write', args: { path: '/etc/hosts' }, decision: 'block', riskTier: 'critical', reversible: false, reason: 'PATH_JAIL — outside workspace' },
      { id: 'tc_5', seq: 5, timestamp: ago(60), tool: 'browser.submit_form', args: { url: 'https://apply.example.org/submit' }, decision: 'ask', riskTier: 'sensitive', reversible: false, reason: 'Awaiting user approval' },
    ],
    budget: { used: 38, total: 100, costUsd: 0.047, maxCostUsd: 0.5 },
    bandwidthTier: 'video',
    pendingApprovals: [
      { id: 'apr_1', tool: 'browser.submit_form', args: { url: 'https://apply.example.org/submit', fields: 6 }, riskTier: 'sensitive', reversible: false, expiresAt: future(90), createdAt: ago(30), summary: 'Submit filled application form' },
    ],
  },
  {
    id: 'sess_def456',
    title: 'Calendar & Reminders deadline scan',
    profile: 'daily_routine',
    state: 'ACTIVE',
    createdAt: ago(600),
    startedAt: ago(580),
    toolCalls: [
      { id: 'tc_a', seq: 1, timestamp: ago(560), tool: 'calendar.list_events', args: { days: 7 }, decision: 'allow', riskTier: 'safe', reversible: true, durationMs: 210 },
      { id: 'tc_b', seq: 2, timestamp: ago(540), tool: 'reminders.fetch', args: { filter: 'due' }, decision: 'allow', riskTier: 'safe', reversible: true, durationMs: 130 },
      { id: 'tc_c', seq: 3, timestamp: ago(500), tool: 'gmail.send', args: { to: 'team@example.com' }, decision: 'block', riskTier: 'critical', reversible: false, reason: 'POLICY_DENY — external send blocked in read-only' },
    ],
    budget: { used: 18, total: 100, costUsd: 0.019, maxCostUsd: 0.5 },
    bandwidthTier: 'screenshots',
    pendingApprovals: [],
  },
  {
    id: 'sess_ghi789',
    title: 'Research report on MCP sandboxing',
    profile: 'general',
    state: 'ENDED',
    createdAt: ago(7200),
    startedAt: ago(7180),
    endedAt: ago(3600),
    toolCalls: [
      { id: 'tc_x', seq: 1, timestamp: ago(7100), tool: 'browser.open', args: { url: 'https://modelcontextprotocol.io' }, decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 920 },
      { id: 'tc_y', seq: 2, timestamp: ago(7000), tool: 'browser.read_page', args: { url: 'https://arxiv.org/abs/2309.00267' }, decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 440 },
    ],
    budget: { used: 24, total: 100, costUsd: 0.031, maxCostUsd: 0.5 },
    bandwidthTier: 'screenshots',
    pendingApprovals: [],
  },
]

export const DEMO_EVENTS: Array<{ delay: number; event: Omit<ToolCallEvent, 'id' | 'seq'> }> = [
  { delay: 1200, event: { timestamp: new Date(), tool: 'files.write', args: { path: '/workspace/fetch_title.py' }, decision: 'allow', riskTier: 'safe', reversible: true, durationMs: 48 } },
  { delay: 2000, event: { timestamp: new Date(), tool: 'terminal.run', args: { cmd: 'pip install requests' }, decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 2100 } },
  { delay: 1800, event: { timestamp: new Date(), tool: 'terminal.run', args: { cmd: 'python fetch_title.py' }, decision: 'allow', riskTier: 'safe', reversible: false, durationMs: 620 } },
  { delay: 1500, event: { timestamp: new Date(), tool: 'files.write', args: { path: '/etc/passwd' }, decision: 'block', riskTier: 'critical', reversible: false, reason: 'PATH_JAIL — target outside /workspace' } },
  { delay: 1800, event: { timestamp: new Date(), tool: 'browser.submit_form', args: { url: 'https://apply.example.org/submit' }, decision: 'ask', riskTier: 'sensitive', reversible: false, reason: 'Awaiting approval' } },
]
