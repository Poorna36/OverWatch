import { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { cn } from '../lib/utils'

type SceneId = 'calendar' | 'form' | 'python' | 'research'
type RunState = 'idle' | 'running' | 'approval' | 'done'

interface FeedItem {
  id: string
  tool: string
  server: string
  argsJson: string
  decision: 'allow' | 'ask' | 'block'
  latencyMs: number
  time: string
  detail: string
  responsePreview: string
}

interface AgentThought {
  id: string
  kind: 'plan' | 'tool' | 'guard' | 'reply'
  text: string
  time: string
}

const SCENES: Record<SceneId, {
  id: SceneId
  label: string
  prompt: string
  windowTitle: string
  profile: string
}> = {
  calendar: {
    id: 'calendar',
    label: '1. Calendar & Deadlines',
    prompt: 'Open my calendar, browse through my reminders and give me deadlines',
    windowTitle: 'Calendar & Reminders — Remote Desktop',
    profile: 'general · calendar + reminders',
  },
  form: {
    id: 'form',
    label: '2. Fill Application Form',
    prompt: 'Go to this specific website and fill an application form for me',
    windowTitle: 'Chromium — https://portal.example.org/apply',
    profile: 'general · browser (guarded submit)',
  },
  python: {
    id: 'python',
    label: '3. Resume Python Project',
    prompt: 'Resume building my project in Python',
    windowTitle: 'VS Code & Terminal — /workspace/overwatch-agent',
    profile: 'code · files + terminal',
  },
  research: {
    id: 'research',
    label: '4. Research & Gmail Draft',
    prompt: 'Research MCP sandboxing papers, summarize findings and email my mentor',
    windowTitle: 'Browser + Gmail — Remote Desktop',
    profile: 'research · browser + gmail (read/draft)',
  },
}

const PIPELINE_STEPS = [
  'Session active',
  'Tool in role allowlist',
  'Arg schema & path jail',
  'Risk tier policy check',
  'Execution budget check',
  'Human approval gate',
  'Execute + hash audit',
]

export function LiveScenePage() {
  const [params] = useSearchParams()
  const initialScene = (params.get('scene') as SceneId) || 'calendar'
  const [sceneId, setSceneId] = useState<SceneId>(SCENES[initialScene] ? initialScene : 'calendar')
  const [runState, setRunState] = useState<RunState>('idle')
  const [step, setStep] = useState(0)
  const [statusText, setStatusText] = useState('Ready to record')
  const [cursor, setCursor] = useState({ x: 14, y: 18, clicking: false, label: 'Overwatch Agent' })
  const cursorRef = useRef({ x: 14, y: 18 })

  const [feed, setFeed] = useState<FeedItem[]>([])
  const [thoughts, setThoughts] = useState<AgentThought[]>([])
  const [activePipeIdx, setActivePipeIdx] = useState(-1)
  const [selectedCallId, setSelectedCallId] = useState<string | null>(null)

  // Form typing state (Scene 2)
  const [formValues, setFormValues] = useState({
    name: '',
    email: '',
    project: '',
    repo: '',
    track: '',
  })

  // Python typing state (Scene 3)
  const [codeSnippet, setCodeSnippet] = useState(
    'async def forward_tool_call(ctx, tool, args):\n    # TODO: add retry & timeout guard\n    return await ctx.gateway.invoke(tool, args)'
  )
  const [termLines, setTermLines] = useState<string[]>([
    'poorn@workstation:~/overwatch-agent$ git status -s',
    ' M src/agent/gateway.py',
    ' M tests/test_policy.py',
  ])

  // Research + Gmail state (Scene 4)
  const [researchNotes, setResearchNotes] = useState<string[]>([])
  const [gmailDraft, setGmailDraft] = useState('')

  const runToken = useRef(0)

  function updateCursor(x: number, y: number, clicking: boolean, label: string) {
    cursorRef.current = { x, y }
    setCursor({ x, y, clicking, label })
  }

  function resetScene(nextId?: SceneId) {
    runToken.current += 1
    if (nextId) setSceneId(nextId)
    setRunState('idle')
    setStep(0)
    setStatusText('Ready to record')
    setFeed([])
    setThoughts([])
    setSelectedCallId(null)
    setActivePipeIdx(-1)
    updateCursor(14, 18, false, 'Overwatch Agent')
    setFormValues({ name: '', email: '', project: '', repo: '', track: '' })
    setCodeSnippet(
      'async def forward_tool_call(ctx, tool, args):\n    # TODO: add retry & timeout guard\n    return await ctx.gateway.invoke(tool, args)'
    )
    setTermLines([
      'poorn@workstation:~/overwatch-agent$ git status -s',
      ' M src/agent/gateway.py',
      ' M tests/test_policy.py',
    ])
    setResearchNotes([])
    setGmailDraft('')
  }

  function addThought(kind: AgentThought['kind'], text: string) {
    const time = new Date().toLocaleTimeString('en-US', { hour12: false })
    setThoughts(prev => [...prev, { id: `${Date.now()}_${Math.random()}`, kind, text, time }])
  }

  function pushFeed(
    tool: string,
    server: string,
    argsJson: string,
    decision: 'allow' | 'ask' | 'block',
    latencyMs: number,
    detail: string,
    responsePreview: string,
  ) {
    const time = new Date().toLocaleTimeString('en-US', { hour12: false })
    const id = `${Date.now()}_${Math.random()}`
    setFeed(prev => [...prev, { id, tool, server, argsJson, decision, latencyMs, time, detail, responsePreview }])
    setSelectedCallId(id)
  }

  async function wait(ms: number, token: number) {
    await new Promise(r => setTimeout(r, ms))
    return runToken.current === token
  }

  /**
   * Moves cursor along a curved multi-step path with natural ease-in-out
   * and slight human-like arc, then pauses briefly before clicking.
   */
  async function moveCursorSmooth(
    targetX: number,
    targetY: number,
    label: string,
    token: number,
    durationMs = 950,
    clickAfter = true,
  ) {
    const startX = cursorRef.current.x
    const startY = cursorRef.current.y
    const steps = Math.max(16, Math.round(durationMs / 28))
    // Slight bezier control offset for organic curved motion
    const arcOffset = ((targetX - startX) * 0.14) + ((startY > targetY ? -1 : 1) * 3.5)
    const ctrlX = (startX + targetX) / 2 + arcOffset * 0.6
    const ctrlY = (startY + targetY) / 2 - Math.abs(arcOffset) * 0.7

    for (let i = 1; i <= steps; i++) {
      if (runToken.current !== token) return false
      const t = i / steps
      // cubic ease-in-out
      const ease = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2
      const inv = 1 - ease
      const x = inv * inv * startX + 2 * inv * ease * ctrlX + ease * ease * targetX
      const y = inv * inv * startY + 2 * inv * ease * ctrlY + ease * ease * targetY
      updateCursor(x, y, false, label)
      await new Promise(r => setTimeout(r, durationMs / steps))
    }

    if (clickAfter) {
      // realistic hover-to-click hesitation
      if (!(await wait(240 + Math.random() * 220, token))) return false
      updateCursor(targetX, targetY, true, label)
      if (!(await wait(280, token))) return false
      updateCursor(targetX, targetY, false, label)
    }
    return true
  }

  /**
   * Natural jittered typing (varies speed per char, pauses slightly on spaces/punctuation)
   */
  async function typeNatural(
    text: string,
    setter: (val: string) => void,
    token: number,
    baseMs = 46,
  ) {
    for (let i = 1; i <= text.length; i++) {
      if (runToken.current !== token) return false
      const ch = text[i - 1]
      setter(text.slice(0, i))
      const jitter = (Math.random() - 0.3) * baseMs * 0.7
      const wordPause = ch === ' ' || ch === '.' || ch === '@' || ch === '/' ? 70 + Math.random() * 90 : 0
      await new Promise(r => setTimeout(r, Math.max(18, baseMs + jitter + wordPause)))
    }
    return true
  }

  async function pulsePipeline(token: number, stopAt?: number, blockedAt?: number) {
    const limit = blockedAt ?? stopAt ?? 6
    for (let i = 0; i <= limit; i++) {
      if (runToken.current !== token) return false
      setActivePipeIdx(i)
      await new Promise(r => setTimeout(r, 110 + Math.random() * 90))
    }
    return true
  }

  async function startExecution() {
    resetScene()
    await new Promise(r => setTimeout(r, 60))
    const token = runToken.current
    setRunState('running')
    setStatusText('... Executing task (analyzing intent)')

    if (sceneId === 'calendar') {
      addThought('plan', 'Parsing intent -> selecting `google-calendar` and `reminders` MCP servers.')
      if (!(await wait(1350, token))) return

      // Step 1: Open Calendar
      setStep(1)
      setStatusText('... Executing task — launching Calendar')
      if (!(await moveCursorSmooth(28, 24, 'Clicking Calendar Week View', token, 1050))) return
      await pulsePipeline(token)
      pushFeed(
        'calendar.open',
        'google-calendar',
        '{ "view": "agenda", "tz": "Asia/Kolkata" }',
        'allow',
        184,
        'Opened Calendar week agenda view',
        '{ "ok": true, "window": "Nov 26 - Dec 03" }',
      )
      if (!(await wait(2900, token))) return

      // Step 2: Scan events asymmetrically
      setStep(2)
      setStatusText('... Executing task — reading calendar events')
      addThought('tool', 'Scanning Nov 26 - Dec 03 schedule for hard deadlines & conflicts.')
      if (!(await moveCursorSmooth(42, 46, 'Inspecting Nov 26-29 events', token, 1180))) return
      await pulsePipeline(token)
      pushFeed(
        'calendar.list_events',
        'google-calendar',
        '{ "start": "2026-11-26", "days": 7 }',
        'allow',
        312,
        'Found 4 upcoming events (2 high priority)',
        '{ "events": 4, "urgent": 2 }',
      )
      if (!(await wait(3700, token))) return

      // Step 3: Browse Reminders drawer
      setStep(3)
      setStatusText('... Executing task — browsing Reminders')
      addThought('tool', 'Switching focus to Reminders pane to cross-check unscheduled tasks.')
      if (!(await moveCursorSmooth(78, 32, 'Opening Reminders list', token, 1250))) return
      await pulsePipeline(token)
      pushFeed(
        'reminders.fetch',
        'reminders',
        '{ "lists": ["Work", "University", "Personal"], "overdue": true }',
        'allow',
        265,
        'Extracted 4 due reminders across 3 lists',
        '{ "count": 4, "earliest": "Today 17:00" }',
      )
      if (!(await wait(4100, token))) return

      // Step 4: Check conflicts & compile reply
      setStep(4)
      setStatusText('... Executing task — compiling briefing for Phone')
      addThought('reply', 'Compiled prioritized deadline briefing (0 conflicts). Sending result to Poorn\'s Phone.')
      if (!(await moveCursorSmooth(75, 76, 'Preparing mobile summary...', token, 900, false))) return
      await pulsePipeline(token)
      pushFeed(
        'bridge.sync_reply',
        'overwatch-bridge',
        '{ "target": "poorn-phone", "items": 4 }',
        'allow',
        92,
        'Delivered deadline summary to mobile chat',
        '{ "delivered": true }',
      )
      if (!(await wait(1400, token))) return
      setStatusText('Completed · Synced to Phone')
      setRunState('done')
      return
    }

    if (sceneId === 'form') {
      addThought('plan', 'Target is a web form -> selecting `browser` MCP server under guarded write policy.')
      if (!(await wait(1200, token))) return

      // Step 1: Navigate
      setStep(1)
      setStatusText('... Executing task — opening portal.example.org')
      if (!(await moveCursorSmooth(42, 14, 'Navigating to form URL', token, 1100))) return
      await pulsePipeline(token)
      pushFeed(
        'browser.navigate',
        'browser',
        '{ "url": "https://portal.example.org/apply" }',
        'allow',
        640,
        'Loaded 2026 Application Portal (5 fields + submit CTA)',
        '{ "status": 200, "inputs": 5, "submitBtn": "#submit-btn" }',
      )
      if (!(await wait(2800, token))) return

      // Step 2: Inspect DOM + type fields naturally
      setStep(2)
      setStatusText('... Executing task — filling application fields')
      addThought('tool', 'Mapping user profile credentials into detected form inputs...')

      if (!(await moveCursorSmooth(34, 29, 'Clicking Full Name', token, 820))) return
      if (!(await typeNatural('Poorn', v => setFormValues(f => ({ ...f, name: v })), token, 62))) return
      if (!(await wait(520, token))) return

      if (!(await moveCursorSmooth(34, 41, 'Clicking Email', token, 680))) return
      if (!(await typeNatural('poorn@overwatch.dev', v => setFormValues(f => ({ ...f, email: v })), token, 48))) return
      if (!(await wait(640, token))) return

      if (!(await moveCursorSmooth(34, 53, 'Entering Project Title', token, 720))) return
      if (!(await typeNatural('Overwatch MCP Control Plane', v => setFormValues(f => ({ ...f, project: v })), token, 40))) return
      if (!(await wait(580, token))) return

      if (!(await moveCursorSmooth(34, 65, 'Entering GitHub Repo', token, 740))) return
      if (!(await typeNatural('https://github.com/poorn/overwatch', v => setFormValues(f => ({ ...f, repo: v })), token, 36))) return
      if (!(await wait(680, token))) return

      if (!(await moveCursorSmooth(34, 76, 'Selecting Track', token, 690))) return
      setFormValues(f => ({ ...f, track: 'AI Systems & Security' }))
      await pulsePipeline(token)
      pushFeed(
        'browser.fill_fields',
        'browser',
        '{ "filled": ["name", "email", "project", "repo", "track"] }',
        'allow',
        418,
        'Populated 5/5 form inputs on page',
        '{ "ok": true, "validationErrors": 0 }',
      )
      if (!(await wait(2400, token))) return

      // Step 3: Move to Submit and HIT POLICY GATE
      setStep(3)
      setStatusText('Paused · Awaiting human approval')
      addThought('guard', 'Gateway intercepted `browser.submit_form` (tier=sensitive, irreversible). Requesting user approval.')
      if (!(await moveCursorSmooth(71, 84, 'Paused before Submit!', token, 1150, false))) return
      await pulsePipeline(token, 5)
      pushFeed(
        'browser.submit_form',
        'browser',
        '{ "selector": "#submit-btn", "irreversible": true }',
        'ask',
        12,
        'Policy gate paused execution prior to clicking Submit',
        '{ "decision": "ask", "rule": "require_approval_for_submit" }',
      )
      setRunState('approval')
      return
    }

    if (sceneId === 'python') {
      addThought('plan', 'Code task -> selecting `filesystem` and `terminal` MCP servers inside /workspace.')
      if (!(await wait(1300, token))) return

      // Step 1: Scan workspace
      setStep(1)
      setStatusText('... Executing task — scanning workspace')
      if (!(await moveCursorSmooth(24, 26, 'Inspecting git diff...', token, 1020))) return
      await pulsePipeline(token)
      pushFeed(
        'files.scan_workspace',
        'filesystem',
        '{ "root": "/workspace/overwatch-agent" }',
        'allow',
        118,
        'Found modified gateway.py and test_policy.py',
        '{ "branch": "feat/retry-guard", "dirty": 2 }',
      )
      if (!(await wait(2600, token))) return

      // Step 2: Guardrail demonstration (attempted write outside workspace -> BLOCKED)
      setStatusText('... Executing task — policy check')
      addThought('guard', 'Agent probed `/etc/overwatch.conf` -> Gateway BLOCKED (PATH_JAIL). Staying inside `/workspace`.')
      if (!(await moveCursorSmooth(18, 42, 'Blocked outside /workspace!', token, 850))) return
      await pulsePipeline(token, undefined, 2)
      pushFeed(
        'files.write',
        'filesystem',
        '{ "path": "/etc/overwatch.conf" }',
        'block',
        6,
        'BLOCKED: target outside workspace jail',
        '{ "error": "PATH_JAIL", "allowedRoot": "/workspace" }',
      )
      if (!(await wait(3100, token))) return

      // Step 3: Edit Python code in workspace
      setStep(2)
      setStatusText('... Executing task — patching src/agent/gateway.py')
      addThought('tool', 'Writing `asyncio.timeout(10.0)` and retry handler inside `/workspace/src/agent/gateway.py`.')
      if (!(await moveCursorSmooth(44, 34, 'Editing gateway.py...', token, 1080))) return
      const updatedCode =
        'async def forward_tool_call(ctx, tool, args):\n' +
        '    async with asyncio.timeout(10.0):\n' +
        '        return await ctx.gateway.invoke_with_retry(\n' +
        '            tool, args, max_retries=2\n' +
        '        )'
      if (!(await typeNatural(updatedCode, setCodeSnippet, token, 26))) return
      await pulsePipeline(token)
      pushFeed(
        'files.write',
        'filesystem',
        '{ "path": "/workspace/overwatch-agent/src/agent/gateway.py" }',
        'allow',
        84,
        'Updated gateway.py (+4 lines, timeout + retry)',
        '{ "bytesWritten": 168 }',
      )
      if (!(await wait(2700, token))) return

      // Step 4: Run pytest
      setStep(3)
      setStatusText('... Executing task — running pytest suite')
      addThought('tool', 'Executing `pytest tests/ -q` to verify no regressions before reporting back.')
      if (!(await moveCursorSmooth(38, 76, 'Running pytest tests/ -q', token, 1100))) return
      setTermLines(l => [...l, 'poorn@workstation:~/overwatch-agent$ pytest tests/ -q'])
      if (!(await wait(2900, token))) return
      setTermLines(l => [...l, '..............                                               [100%]', '14 passed in 0.84s'])
      await pulsePipeline(token)
      pushFeed(
        'terminal.run',
        'terminal',
        '{ "cmd": "pytest tests/ -q", "cwd": "/workspace/overwatch-agent" }',
        'allow',
        842,
        'All 14 tests passed in 0.84s',
        '{ "exitCode": 0, "passed": 14, "failed": 0 }',
      )
      if (!(await wait(1900, token))) return

      setStep(4)
      addThought('reply', 'Python build healthy (14/14 passing). Sent status update to mobile.')
      updateCursor(62, 80, false, 'Build clean · Synced to Phone')
      setStatusText('Completed · Synced to Phone')
      setRunState('done')
      return
    }

    if (sceneId === 'research') {
      addThought('plan', 'Research + Email task -> selecting `browser` (read) and `gmail` (draft-only).')
      if (!(await wait(1200, token))) return

      // Step 1: Search papers
      setStep(1)
      setStatusText('... Executing task — searching MCP papers')
      if (!(await moveCursorSmooth(28, 26, 'Searching arXiv & MCP spec...', token, 1080))) return
      await pulsePipeline(token)
      setResearchNotes(['1. Fetching arXiv:2309.00267 (Agent Sandboxing)...'])
      pushFeed(
        'browser.search',
        'browser',
        '{ "q": "MCP tool sandboxing human approval", "limit": 5 }',
        'allow',
        512,
        'Retrieved 5 peer-reviewed sources & MCP spec',
        '{ "results": 5, "topSource": "modelcontextprotocol.io" }',
      )
      if (!(await wait(3200, token))) return

      // Step 2: Synthesize findings
      setStep(2)
      setStatusText('... Executing task — reading & summarizing findings')
      addThought('tool', 'Extracting 3 key architectural takeaways for mentor email.')
      if (!(await moveCursorSmooth(34, 48, 'Extracting key findings...', token, 980))) return
      setResearchNotes([
        '1. Capability-scoped MCP servers cut blast radius by 88%.',
        '2. Path-jail + egress allowlists prevent prompt-injected exfiltration.',
        '3. Human-in-the-loop pause on irreversible actions preserves trust.',
      ])
      await pulsePipeline(token)
      pushFeed(
        'browser.read_page',
        'browser',
        '{ "url": "https://arxiv.org/abs/2309.00267" }',
        'allow',
        445,
        'Parsed 3 key security takeaways',
        '{ "tokensRead": 3420 }',
      )
      if (!(await wait(3500, token))) return

      // Step 3: Draft Gmail (Allowed)
      setStep(3)
      setStatusText('... Executing task — drafting Gmail reply')
      addThought('tool', 'Composing email draft to mentor inside Gmail.')
      if (!(await moveCursorSmooth(74, 38, 'Drafting email in Gmail...', token, 1120))) return
      const draft = 'Hi Dr. Rao,\n\nHere are 3 findings on MCP sandboxing for our Overwatch demo:\n- Scoped MCP tool profiles\n- Path-jail enforcement\n- Approval gates before submit\n\nBest,\nPoorn'
      if (!(await typeNatural(draft, setGmailDraft, token, 24))) return
      await pulsePipeline(token)
      pushFeed(
        'gmail.create_draft',
        'gmail',
        '{ "to": "mentor@university.edu", "subject": "MCP Research Notes" }',
        'allow',
        290,
        'Saved Gmail draft safely (not sent yet)',
        '{ "draftId": "dr_8841", "status": "saved" }',
      )
      if (!(await wait(2400, token))) return

      // Step 4: Policy blocks auto-send
      setStep(4)
      setStatusText('Completed · Draft saved (Auto-send blocked)')
      addThought('guard', '`gmail.send` is blocked in Research role. Draft saved so you stay in control.')
      if (!(await moveCursorSmooth(80, 78, 'Send blocked · Draft ready', token, 920))) return
      await pulsePipeline(token, undefined, 3)
      pushFeed(
        'gmail.send',
        'gmail',
        '{ "draftId": "dr_8841" }',
        'block',
        8,
        'POLICY_DENY: autonomous external send blocked',
        '{ "error": "DRAFT_ONLY_PROFILE" }',
      )
      setRunState('done')
    }
  }

  async function handleApproveForm() {
    const token = runToken.current
    setStatusText('... Executing task — submitting form')
    if (!(await moveCursorSmooth(73, 84, 'Clicking Submit Application', token, 700))) return
    setActivePipeIdx(6)
    await new Promise(r => setTimeout(r, 950))
    if (runToken.current !== token) return
    pushFeed(
      'browser.submit_form',
      'browser',
      '{ "approvedBy": "poorn", "selector": "#submit-btn" }',
      'allow',
      710,
      'Submitted form · Receipt #APP-2048 synced to Phone',
      '{ "submitted": true, "receipt": "APP-2048" }',
    )
    addThought('reply', 'Form submitted after human approval (#APP-2048). Sent confirmation to phone.')
    setStep(4)
    setStatusText('Completed · Synced to Phone')
    setRunState('done')
  }

  function handleDenyForm() {
    addThought('guard', 'Submission cancelled by user. Form left unsubmitted.')
    pushFeed(
      'browser.submit_form',
      'browser',
      '{ "action": "denied_by_user" }',
      'block',
      5,
      'User denied external submission',
      '{ "cancelled": true }',
    )
    setStatusText('Cancelled by user')
    setRunState('idle')
  }

  useEffect(() => {
    const q = params.get('scene') as SceneId | null
    if (q && SCENES[q]) setSceneId(q)
  }, [params])

  const scene = SCENES[sceneId]
  const selectedCall = feed.find(f => f.id === selectedCallId) ?? feed[feed.length - 1] ?? null

  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-[#141413]">
      {/* Top Bar — Recording Controls */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 pl-24 border-b border-[#33312D] bg-[#1C1B19]">
        <div className="flex items-center gap-2.5 flex-wrap">
          <div className="flex items-center gap-2 px-2.5 py-1 rounded-full bg-[#17281E] border border-[#6BBF8E]/40">
            <span className="w-2 h-2 rounded-full bg-[#6BBF8E] animate-pulse" />
            <span className="text-[11px] font-mono text-[#6BBF8E]">DESKTOP AGENT SCENE</span>
          </div>
          <div className="flex gap-1.5 flex-wrap">
            {(Object.values(SCENES)).map(sc => (
              <button
                key={sc.id}
                onClick={() => resetScene(sc.id)}
                className={cn(
                  'px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-all',
                  sceneId === sc.id
                    ? 'border-[#D97757] bg-[#332018] text-[#D97757]'
                    : 'border-[#33312D] bg-[#141413] text-[#B0AAA0] hover:border-[#45423C]',
                )}
              >
                {sc.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => resetScene()}
            className="px-3 py-1.5 rounded-lg text-xs border border-[#33312D] text-[#B0AAA0] hover:text-[#F4F1EA]"
          >
            Reset
          </button>
          <button
            onClick={startExecution}
            disabled={runState === 'running'}
            className="px-4 py-1.5 rounded-lg text-xs font-bold bg-[#D97757] text-[#141413] hover:bg-[#e08568] disabled:opacity-50"
          >
            {runState === 'running' ? '● Agent Working…' : '▶ Run Live Task'}
          </button>
        </div>
      </div>

      {/* Main Split: Desktop Workspace + MCP Protocol Inspector */}
      <div className="flex-1 flex overflow-hidden">
        {/* LEFT: Simulated Remote Desktop Screen */}
        <div className="flex-1 p-4 flex flex-col gap-3 overflow-hidden">
          {/* Incoming mobile banner */}
          <div className="flex items-center justify-between px-4 py-2.5 rounded-xl border border-[#33312D] bg-[#1C1B19]">
            <div className="text-xs text-[#B0AAA0] truncate pr-3">
              <span className="text-[#D97757] font-semibold">Incoming from Phone: </span>
              “{scene.prompt}”
            </div>
            <span className={cn(
              'text-[11px] font-mono px-2.5 py-0.5 rounded-full border shrink-0',
              runState === 'running' && 'text-[#D97757] border-[#D97757]/40 bg-[#332018] animate-pulse',
              runState === 'approval' && 'text-[#E5A93C] border-[#E5A93C]/40 bg-[#2C2210]',
              runState === 'done' && 'text-[#6BBF8E] border-[#6BBF8E]/40 bg-[#17281E]',
              runState === 'idle' && 'text-[#787268] border-[#33312D] bg-[#141413]',
            )}>
              {statusText}
            </span>
          </div>

          {/* Desktop Window Frame */}
          <div className="flex-1 relative rounded-2xl border border-[#45423C] bg-[#0E0E0D] shadow-2xl overflow-hidden flex flex-col">
            {/* Window Chrome */}
            <div className="flex items-center justify-between px-4 py-2 border-b border-[#33312D] bg-[#1C1B19]">
              <div className="flex items-center gap-2">
                <div className="flex gap-1.5">
                  <span className="w-3 h-3 rounded-full bg-[#E06C65]/80" />
                  <span className="w-3 h-3 rounded-full bg-[#E5A93C]/80" />
                  <span className="w-3 h-3 rounded-full bg-[#6BBF8E]/80" />
                </div>
                <span className="text-xs font-mono text-[#B0AAA0] ml-2">{scene.windowTitle}</span>
              </div>
              <span className="text-[10px] font-mono text-[#787268]">RDP · {scene.profile}</span>
            </div>

            {/* Scene Body */}
            <div className="flex-1 p-4 overflow-y-auto relative">
              {/* ─── SCENE 1: CALENDAR & REMINDERS ─── */}
              {sceneId === 'calendar' && (
                <div className="grid grid-cols-12 gap-4 h-full">
                  <div className="col-span-7 rounded-xl border border-[#33312D] bg-[#141413] p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold text-[#F4F1EA]">November 2026</span>
                      <span className={cn(
                        'text-[11px] font-mono px-2 py-0.5 rounded border',
                        step >= 2 ? 'border-[#D97757] bg-[#332018] text-[#D97757]' : 'border-[#33312D] text-[#787268]',
                      )}>
                        Week Agenda
                      </span>
                    </div>
                    <div className="space-y-2">
                      {[
                        { day: 'Thu 26 (Today)', title: 'Overwatch Architecture Review', time: '5:00 PM', urgent: true },
                        { day: 'Fri 27', title: 'Hackathon Portal Submission', time: '11:59 PM', urgent: true },
                        { day: 'Sat 29', title: 'API Security & Policy Check', time: '2:00 PM', urgent: false },
                        { day: 'Mon 01', title: 'Sprint Demo & Retrospective', time: '10:00 AM', urgent: false },
                      ].map((ev, idx) => (
                        <div
                          key={ev.day}
                          className={cn(
                            'rounded-lg border p-3 transition-all duration-500 flex items-center justify-between',
                            step >= 2 && idx < 3
                              ? 'border-[#D97757]/60 bg-[#332018]/40'
                              : 'border-[#33312D] bg-[#1C1B19]',
                          )}
                        >
                          <div>
                            <p className="text-[11px] font-mono text-[#787268]">{ev.day}</p>
                            <p className="text-xs font-semibold text-[#F4F1EA] mt-0.5">{ev.title}</p>
                          </div>
                          <span className={cn('text-xs font-mono', ev.urgent ? 'text-[#E5A93C]' : 'text-[#B0AAA0]')}>
                            {ev.time}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="col-span-5 rounded-xl border border-[#33312D] bg-[#141413] p-4 flex flex-col justify-between">
                    <div className="space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-semibold text-[#F4F1EA]">Reminders</span>
                        <span className="text-[11px] font-mono text-[#6BBF8E]">
                          {step >= 3 ? '4 scanned' : 'Waiting'}
                        </span>
                      </div>
                      <div className="space-y-2">
                        {[
                          { text: 'Overwatch Demo Deck', due: 'Today 17:00', tag: 'URGENT' },
                          { text: 'Hackathon Submission Form', due: 'Nov 27 23:59', tag: 'HIGH' },
                          { text: 'API Security Review', due: 'Nov 29 14:00', tag: 'WORK' },
                          { text: 'Sprint Retrospective', due: 'Dec 01 10:00', tag: 'TEAM' },
                        ].map((r) => (
                          <div
                            key={r.text}
                            className={cn(
                              'rounded-lg border p-2.5 text-xs transition-all duration-500',
                              step >= 3
                                ? 'border-[#6BBF8E]/50 bg-[#17281E]/50 opacity-100'
                                : 'border-[#33312D] bg-[#1C1B19] opacity-55',
                            )}
                          >
                            <div className="flex justify-between">
                              <span className="font-medium text-[#F4F1EA]">{step >= 3 ? `✓ ${r.text}` : r.text}</span>
                              <span className="text-[10px] font-mono text-[#E5A93C]">{r.tag}</span>
                            </div>
                            <p className="text-[11px] font-mono text-[#787268] mt-1">{r.due}</p>
                          </div>
                        ))}
                      </div>
                    </div>

                    {step >= 4 && (
                      <div className="rounded-lg border border-[#6BBF8E]/50 bg-[#17281E] p-3 text-xs text-[#6BBF8E]">
                        ✓ Extracted 4 deadlines & synced briefing back to Mobile Chat.
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* ─── SCENE 2: WEBSITE APPLICATION FORM ─── */}
              {sceneId === 'form' && (
                <div className="max-w-xl mx-auto rounded-xl border border-[#33312D] bg-[#141413] p-5 space-y-3.5">
                  <div className="border-b border-[#33312D] pb-3 flex items-center justify-between">
                    <div>
                      <h3 className="text-sm font-bold text-[#F4F1EA]">2026 AI Systems Application Form</h3>
                      <p className="text-[11px] text-[#787268]">portal.example.org/apply · Secured Portal</p>
                    </div>
                    {step >= 4 && (
                      <span className="text-xs font-mono px-2.5 py-1 rounded bg-[#17281E] text-[#6BBF8E] border border-[#6BBF8E]/40">
                        SUBMITTED #APP-2048
                      </span>
                    )}
                  </div>

                  <div className="space-y-2.5">
                    {[
                      { label: 'Full Name', val: formValues.name },
                      { label: 'Email Address', val: formValues.email },
                      { label: 'Project Title', val: formValues.project },
                      { label: 'GitHub Repository', val: formValues.repo },
                      { label: 'Category Track', val: formValues.track },
                    ].map(field => (
                      <div key={field.label}>
                        <label className="block text-[11px] text-[#787268] mb-1">{field.label}</label>
                        <div className={cn(
                          'w-full rounded-lg border px-3 py-2 text-xs font-mono min-h-[32px] flex items-center transition-colors',
                          field.val
                            ? 'border-[#D97757]/60 bg-[#1C1B19] text-[#F4F1EA]'
                            : 'border-[#33312D] bg-[#0E0E0D] text-[#4A463F]',
                        )}>
                          {field.val || 'Waiting for agent input…'}
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="pt-1 flex items-center justify-between gap-3">
                    <span className="text-[11px] text-[#787268]">
                      {runState === 'approval'
                        ? 'Paused by MCP Gateway before final submission'
                        : step >= 4
                          ? 'Confirmation synced to phone'
                          : 'Form ready'}
                    </span>
                    <button
                      onClick={runState === 'approval' ? handleApproveForm : undefined}
                      className={cn(
                        'px-4 py-2 rounded-lg text-xs font-bold transition-all',
                        step >= 4
                          ? 'bg-[#6BBF8E] text-[#141413]'
                          : runState === 'approval'
                            ? 'bg-[#E5A93C] text-[#141413] animate-pulse'
                            : 'bg-[#33312D] text-[#787268]',
                      )}
                    >
                      {step >= 4 ? 'Submitted ✓' : 'Submit Application'}
                    </button>
                  </div>

                  {runState === 'approval' && (
                    <div className="rounded-xl border border-[#E5A93C]/50 bg-[#2C2210] p-3.5 flex items-center justify-between gap-3">
                      <div>
                        <p className="text-xs font-bold text-[#E5A93C]">MCP Policy Gate: Confirm Submission</p>
                        <p className="text-[11px] text-[#B0AAA0]">Submitting external forms is irreversible.</p>
                      </div>
                      <div className="flex gap-2">
                        <button
                          onClick={handleDenyForm}
                          className="px-3 py-1.5 rounded-lg text-xs border border-[#33312D] bg-[#1C1B19] text-[#B0AAA0]"
                        >
                          Deny
                        </button>
                        <button
                          onClick={handleApproveForm}
                          className="px-3.5 py-1.5 rounded-lg text-xs font-bold bg-[#D97757] text-[#141413]"
                        >
                          Approve & Submit
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* ─── SCENE 3: PYTHON PROJECT BUILD ─── */}
              {sceneId === 'python' && (
                <div className="flex flex-col gap-3 h-full">
                  <div className="rounded-xl border border-[#33312D] bg-[#141413] p-4 flex-1">
                    <div className="flex items-center justify-between mb-2 pb-2 border-b border-[#33312D]">
                      <span className="text-xs font-mono text-[#D97757]">src/agent/gateway.py</span>
                      <span className="text-[10px] font-mono text-[#787268]">Python 3.12 · UTF-8</span>
                    </div>
                    <pre className="text-xs font-mono text-[#F4F1EA] leading-relaxed whitespace-pre-wrap">
                      {codeSnippet}
                    </pre>
                  </div>

                  <div className="rounded-xl border border-[#33312D] bg-[#0B0B0A] p-4 h-40 overflow-y-auto font-mono text-xs space-y-1">
                    <div className="text-[10px] text-[#787268] mb-1">TERMINAL · bash</div>
                    {termLines.map((line, i) => (
                      <p
                        key={i}
                        className={cn(line.includes('passed') ? 'text-[#6BBF8E] font-semibold' : 'text-[#B0AAA0]')}
                      >
                        {line}
                      </p>
                    ))}
                    {runState === 'running' && (
                      <span className="inline-block w-2 h-3.5 bg-[#D97757] animate-pulse" />
                    )}
                  </div>
                </div>
              )}

              {/* ─── SCENE 4: RESEARCH & GMAIL DRAFT ─── */}
              {sceneId === 'research' && (
                <div className="grid grid-cols-12 gap-4 h-full">
                  <div className="col-span-6 rounded-xl border border-[#33312D] bg-[#141413] p-4 space-y-3">
                    <div className="flex items-center justify-between border-b border-[#33312D] pb-2">
                      <span className="text-xs font-semibold text-[#F4F1EA]">Browser — arXiv & MCP Spec</span>
                      <span className="text-[10px] font-mono text-[#6BBF8E]">read-only</span>
                    </div>
                    {researchNotes.length === 0 ? (
                      <p className="text-xs text-[#787268]">Waiting for browser.search tool call...</p>
                    ) : (
                      <div className="space-y-2">
                        {researchNotes.map((n, i) => (
                          <div key={i} className="rounded-lg border border-[#33312D] bg-[#1C1B19] p-2.5 text-xs text-[#F4F1EA]">
                            {n}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="col-span-6 rounded-xl border border-[#33312D] bg-[#141413] p-4 flex flex-col justify-between space-y-3">
                    <div className="space-y-2 flex-1">
                      <div className="flex items-center justify-between border-b border-[#33312D] pb-2">
                        <span className="text-xs font-semibold text-[#F4F1EA]">Gmail — New Draft</span>
                        <span className="text-[10px] font-mono text-[#E5A93C]">send blocked</span>
                      </div>
                      <div className="text-[11px] font-mono text-[#787268]">To: mentor@university.edu</div>
                      <pre className="text-xs font-mono text-[#F4F1EA] whitespace-pre-wrap leading-relaxed pt-1">
                        {gmailDraft || 'Waiting to compose draft...'}
                      </pre>
                    </div>
                    {step >= 4 && (
                      <div className="rounded-lg border border-[#E06C65]/40 bg-[#2D1817] p-2.5 text-[11px] text-[#E06C65]">
                        ✕ `gmail.send` blocked by policy — draft saved for your review.
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* ─── Animated AI Agent Cursor (smooth Bezier coordinates) ─── */}
              {runState !== 'idle' && (
                <div
                  className="pointer-events-none absolute z-30"
                  style={{ left: `${cursor.x}%`, top: `${cursor.y}%` }}
                >
                  <div className="relative">
                    <svg width="20" height="20" viewBox="0 0 24 24" className="drop-shadow-lg">
                      <path
                        d="M4 2L20 11L12 13.5L9.5 21L4 2Z"
                        fill="#D97757"
                        stroke="#141413"
                        strokeWidth="1.8"
                      />
                    </svg>
                    {cursor.clicking && (
                      <span className="absolute -top-1 -left-1 w-6 h-6 rounded-full border-2 border-[#D97757] animate-ping opacity-80" />
                    )}
                    <div className="ml-4 -mt-1 px-2 py-0.5 rounded-md bg-[#D97757] text-[#141413] text-[10px] font-bold whitespace-nowrap shadow">
                      {cursor.label}
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Agent Reasoning / Thought Strip */}
            <div className="border-t border-[#33312D] bg-[#1C1B19] px-4 py-2.5 max-h-28 overflow-y-auto space-y-1">
              <div className="text-[10px] font-bold uppercase tracking-wider text-[#787268]">
                Agent Reasoning & Response Log
              </div>
              {thoughts.length === 0 ? (
                <p className="text-xs text-[#787268]">Click <strong className="text-[#D97757]">▶ Run Live Task</strong> to stream agent reasoning and desktop execution.</p>
              ) : (
                thoughts.map(th => (
                  <div key={th.id} className="flex items-start gap-2 text-xs">
                    <span className={cn(
                      'text-[10px] font-mono uppercase px-1.5 py-0.2 rounded shrink-0 mt-0.5',
                      th.kind === 'plan' && 'bg-[#332018] text-[#D97757]',
                      th.kind === 'tool' && 'bg-[#242320] text-[#B0AAA0]',
                      th.kind === 'guard' && 'bg-[#2C2210] text-[#E5A93C]',
                      th.kind === 'reply' && 'bg-[#17281E] text-[#6BBF8E]',
                    )}>
                      {th.kind}
                    </span>
                    <span className="text-[#F4F1EA]/90">{th.text}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* RIGHT: MCP Control Plane & JSON-RPC Inspector */}
        <div className="w-[360px] shrink-0 border-l border-[#33312D] bg-[#1C1B19] flex flex-col overflow-hidden">
          <div className="p-4 border-b border-[#33312D]">
            <p className="text-xs font-bold text-[#F4F1EA]">MCP Gateway Inspector</p>
            <p className="text-[11px] text-[#787268] mt-0.5">Policy pipeline + JSON-RPC tool stream</p>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {/* Pipeline */}
            <div>
              <p className="text-[10px] font-bold uppercase tracking-widest text-[#787268] mb-2">
                Gateway Pipeline
              </p>
              <div className="space-y-1">
                {PIPELINE_STEPS.map((label, idx) => {
                  const passed = idx <= activePipeIdx
                  const waiting = runState === 'approval' && idx === 5
                  const blocked = selectedCall?.decision === 'block' && idx === activePipeIdx
                  return (
                    <div
                      key={label}
                      className="flex items-center justify-between text-xs px-2.5 py-1.5 rounded-lg bg-[#141413] border border-[#33312D]"
                    >
                      <span className={passed ? 'text-[#F4F1EA]' : 'text-[#787268]'}>{label}</span>
                      <span className={cn(
                        'text-[10px] font-mono',
                        blocked ? 'text-[#E06C65]' : waiting ? 'text-[#E5A93C]' : passed ? 'text-[#6BBF8E]' : 'text-[#4A463F]',
                      )}>
                        {blocked ? 'BLOCK' : waiting ? 'WAIT' : passed ? 'PASS' : '—'}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Live Tool Calls */}
            <div>
              <p className="text-[10px] font-bold uppercase tracking-widest text-[#787268] mb-2">
                MCP Calls ({feed.length})
              </p>
              <div className="space-y-2">
                {feed.map(item => (
                  <button
                    key={item.id}
                    onClick={() => setSelectedCallId(item.id)}
                    className={cn(
                      'w-full text-left rounded-xl border p-2.5 space-y-1 transition-all',
                      selectedCall?.id === item.id
                        ? 'border-[#D97757]/60 bg-[#242320]'
                        : 'border-[#33312D] bg-[#141413] hover:border-[#45423C]',
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-mono font-semibold text-[#F4F1EA]">{item.tool}</span>
                      <span className={cn(
                        'text-[10px] font-mono px-1.5 py-0.5 rounded',
                        item.decision === 'allow' && 'bg-[#17281E] text-[#6BBF8E]',
                        item.decision === 'ask' && 'bg-[#2C2210] text-[#E5A93C]',
                        item.decision === 'block' && 'bg-[#2D1817] text-[#E06C65]',
                      )}>
                        {item.decision} · {item.latencyMs}ms
                      </span>
                    </div>
                    <p className="text-[11px] text-[#B0AAA0]">{item.detail}</p>
                  </button>
                ))}
              </div>
            </div>

            {/* JSON-RPC Frame Inspector */}
            {selectedCall && (
              <div>
                <p className="text-[10px] font-bold uppercase tracking-widest text-[#787268] mb-2">
                  JSON-RPC Frame
                </p>
                <div className="rounded-xl border border-[#33312D] bg-[#0E0E0D] p-3 font-mono text-[10px] space-y-1.5 text-[#B0AAA0]">
                  <p className="text-[#D97757]">// server: {selectedCall.server}</p>
                  <p>{`"method": "tools/call",`}</p>
                  <p>{`"name": "${selectedCall.tool}",`}</p>
                  <p className="text-[#F4F1EA] break-all">{`"arguments": ${selectedCall.argsJson}`}</p>
                  <p className="pt-1 border-t border-[#33312D] text-[#6BBF8E] break-all">
                    {`<- ${selectedCall.responsePreview}`}
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
