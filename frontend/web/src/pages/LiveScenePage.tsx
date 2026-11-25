import { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { cn } from '../lib/utils'

type SceneId = 'calendar' | 'form' | 'python'
type RunState = 'idle' | 'running' | 'approval' | 'done'

interface FeedItem {
  id: string
  tool: string
  args: string
  decision: 'allow' | 'ask' | 'block'
  time: string
  detail: string
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
}

const PIPELINE_STEPS = [
  'Session active',
  'Tool in role allowlist',
  'Argument schema & path jail',
  'Risk tier policy check',
  'Execution budget check',
  'Human approval gate',
  'Execute + hash-chain audit',
]

export function LiveScenePage() {
  const [params] = useSearchParams()
  const initialScene = (params.get('scene') as SceneId) || 'calendar'
  const [sceneId, setSceneId] = useState<SceneId>(SCENES[initialScene] ? initialScene : 'calendar')
  const [runState, setRunState] = useState<RunState>('idle')
  const [step, setStep] = useState(0)
  const [cursor, setCursor] = useState({ x: 18, y: 20, clicking: false, label: 'Overwatch Agent' })
  const [feed, setFeed] = useState<FeedItem[]>([])
  const [activePipeIdx, setActivePipeIdx] = useState(-1)

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

  const runToken = useRef(0)

  function resetScene(nextId?: SceneId) {
    runToken.current += 1
    if (nextId) setSceneId(nextId)
    setRunState('idle')
    setStep(0)
    setFeed([])
    setActivePipeIdx(-1)
    setCursor({ x: 18, y: 20, clicking: false, label: 'Overwatch Agent' })
    setFormValues({ name: '', email: '', project: '', repo: '', track: '' })
    setCodeSnippet(
      'async def forward_tool_call(ctx, tool, args):\n    # TODO: add retry & timeout guard\n    return await ctx.gateway.invoke(tool, args)'
    )
    setTermLines([
      'poorn@workstation:~/overwatch-agent$ git status -s',
      ' M src/agent/gateway.py',
      ' M tests/test_policy.py',
    ])
  }

  function pushFeed(tool: string, args: string, decision: 'allow' | 'ask' | 'block', detail: string) {
    const now = new Date().toLocaleTimeString('en-US', { hour12: false })
    setFeed(prev => [...prev, { id: `${Date.now()}_${Math.random()}`, tool, args, decision, time: now, detail }])
  }

  async function wait(ms: number, token: number) {
    await new Promise(r => setTimeout(r, ms))
    return runToken.current === token
  }

  async function typeInto(
    text: string,
    setter: (val: string) => void,
    token: number,
    speedMs = 36,
  ) {
    for (let i = 1; i <= text.length; i++) {
      if (runToken.current !== token) return false
      setter(text.slice(0, i))
      await new Promise(r => setTimeout(r, speedMs))
    }
    return true
  }

  async function pulsePipeline(token: number, stopAtApproval = false) {
    const end = stopAtApproval ? 5 : 6
    for (let i = 0; i <= end; i++) {
      if (runToken.current !== token) return false
      setActivePipeIdx(i)
      await new Promise(r => setTimeout(r, 140))
    }
    return true
  }

  async function startExecution() {
    resetScene()
    // wait a tick so state resets cleanly before starting
    await new Promise(r => setTimeout(r, 40))
    const token = runToken.current
    setRunState('running')

    if (sceneId === 'calendar') {
      // Step 1: Open Calendar
      setStep(1)
      setCursor({ x: 24, y: 26, clicking: true, label: 'Opening Calendar...' })
      await pulsePipeline(token)
      pushFeed('calendar.open', 'range=Nov26..Dec03', 'allow', 'Mounted local Calendar store')
      if (!(await wait(2400, token))) return

      // Step 2: Scan Week Events
      setStep(2)
      setCursor({ x: 46, y: 48, clicking: true, label: 'Scanning events...' })
      await pulsePipeline(token)
      pushFeed('calendar.list_events', 'days=7', 'allow', 'Found 6 calendar events')
      if (!(await wait(2600, token))) return

      // Step 3: Browse Reminders drawer
      setStep(3)
      setCursor({ x: 76, y: 42, clicking: true, label: 'Browsing Reminders...' })
      await pulsePipeline(token)
      pushFeed('reminders.fetch', 'lists=[Work,Uni,Personal]', 'allow', 'Extracted 4 due deadlines')
      if (!(await wait(2800, token))) return

      // Step 4: Compile & sync back to phone
      setStep(4)
      setCursor({ x: 72, y: 78, clicking: false, label: 'Syncing to Phone...' })
      pushFeed('bridge.sync_reply', 'target=mobile', 'allow', 'Deadlines sent to phone')
      setRunState('done')
      return
    }

    if (sceneId === 'form') {
      // Step 1: Navigate
      setStep(1)
      setCursor({ x: 38, y: 15, clicking: true, label: 'Loading form...' })
      await pulsePipeline(token)
      pushFeed('browser.navigate', 'portal.example.org/apply', 'allow', 'Page loaded (6 inputs detected)')
      if (!(await wait(1600, token))) return

      // Step 2: Fill fields visibly
      setStep(2)
      setCursor({ x: 35, y: 31, clicking: true, label: 'Typing Name...' })
      if (!(await typeInto('Poorn', v => setFormValues(f => ({ ...f, name: v })), token, 55))) return

      setCursor({ x: 35, y: 43, clicking: true, label: 'Typing Email...' })
      if (!(await typeInto('poorn@overwatch.dev', v => setFormValues(f => ({ ...f, email: v })), token, 40))) return

      setCursor({ x: 35, y: 55, clicking: true, label: 'Typing Project...' })
      if (!(await typeInto('Overwatch MCP Control Plane', v => setFormValues(f => ({ ...f, project: v })), token, 34))) return

      setCursor({ x: 35, y: 67, clicking: true, label: 'Typing GitHub...' })
      if (!(await typeInto('https://github.com/poorn/overwatch', v => setFormValues(f => ({ ...f, repo: v })), token, 32))) return

      setCursor({ x: 35, y: 77, clicking: true, label: 'Selecting Track...' })
      setFormValues(f => ({ ...f, track: 'AI Systems & Security' }))
      await pulsePipeline(token)
      pushFeed('browser.fill_fields', 'fields=5', 'allow', 'Auto-filled profile fields')
      if (!(await wait(1400, token))) return

      // Step 3: Move toward Submit and PAUSE for permission
      setStep(3)
      setCursor({ x: 72, y: 86, clicking: false, label: 'Paused before Submit!' })
      await pulsePipeline(token, true)
      pushFeed('browser.submit_form', 'click #submit-btn', 'ask', 'Irreversible submit — paused by policy')
      setRunState('approval')
      return
    }

    if (sceneId === 'python') {
      // Step 1: Scan workspace
      setStep(1)
      setCursor({ x: 26, y: 30, clicking: true, label: 'Reading gateway.py...' })
      await pulsePipeline(token)
      pushFeed('files.scan_workspace', '/workspace/overwatch-agent', 'allow', 'Located unfinished gateway.py')
      if (!(await wait(2000, token))) return

      // Step 2: Edit Python code live
      setStep(2)
      setCursor({ x: 48, y: 40, clicking: true, label: 'Writing timeout guard...' })
      const updatedCode =
        'async def forward_tool_call(ctx, tool, args):\n' +
        '    async with asyncio.timeout(10.0):\n' +
        '        return await ctx.gateway.invoke_with_retry(\n' +
        '            tool, args, max_retries=2\n' +
        '        )'
      if (!(await typeInto(updatedCode, setCodeSnippet, token, 22))) return
      await pulsePipeline(token)
      pushFeed('files.write', 'src/agent/gateway.py', 'allow', 'Added asyncio.timeout & retry guard')
      if (!(await wait(1600, token))) return

      // Step 3: Run pytest in terminal
      setStep(3)
      setCursor({ x: 42, y: 78, clicking: true, label: 'Running pytest...' })
      setTermLines(l => [...l, 'poorn@workstation:~/overwatch-agent$ pytest tests/ -q'])
      if (!(await wait(1800, token))) return
      setTermLines(l => [...l, '..............                                               [100%]', '14 passed in 0.84s'])
      await pulsePipeline(token)
      pushFeed('terminal.run', 'pytest tests/ -q', 'allow', '14 passed in 0.84s (exit 0)')
      if (!(await wait(1200, token))) return

      setStep(4)
      setCursor({ x: 64, y: 82, clicking: false, label: 'Build ready · Synced to Phone' })
      setRunState('done')
    }
  }

  async function handleApproveForm() {
    const token = runToken.current
    setCursor({ x: 74, y: 86, clicking: true, label: 'Clicking Submit...' })
    setActivePipeIdx(6)
    await new Promise(r => setTimeout(r, 800))
    if (runToken.current !== token) return
    pushFeed('browser.submit_form', 'approved by user', 'allow', 'Submitted · Receipt #APP-2048')
    setStep(4)
    setRunState('done')
  }

  function handleDenyForm() {
    pushFeed('browser.submit_form', 'denied by user', 'block', 'Cancelled before submit')
    resetScene()
  }

  // Auto-select scene from URL query on mount
  useEffect(() => {
    const q = params.get('scene') as SceneId | null
    if (q && SCENES[q]) setSceneId(q)
  }, [params])

  const scene = SCENES[sceneId]

  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-[#141413]">
      {/* Top Bar — Recording Controls */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 border-b border-[#33312D] bg-[#1C1B19]">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 px-2.5 py-1 rounded-full bg-[#17281E] border border-[#6BBF8E]/40">
            <span className="w-2 h-2 rounded-full bg-[#6BBF8E] animate-pulse" />
            <span className="text-[11px] font-mono text-[#6BBF8E]">DESKTOP AGENT SCENE</span>
          </div>
          <div className="flex gap-1.5">
            {(Object.values(SCENES)).map(sc => (
              <button
                key={sc.id}
                onClick={() => resetScene(sc.id)}
                className={cn(
                  'px-3 py-1.5 rounded-lg text-xs font-medium border transition-all',
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

      {/* Main Split: 65% Desktop Workspace / 35% MCP Control Plane */}
      <div className="flex-1 flex overflow-hidden">
        {/* LEFT: Simulated Remote Desktop Screen */}
        <div className="flex-1 p-5 flex flex-col gap-3 overflow-hidden">
          {/* Incoming mobile banner */}
          <div className="flex items-center justify-between px-4 py-2.5 rounded-xl border border-[#33312D] bg-[#1C1B19]">
            <div className="text-xs text-[#B0AAA0]">
              <span className="text-[#D97757] font-semibold">Incoming from Phone: </span>
              “{scene.prompt}”
            </div>
            <span className={cn(
              'text-[11px] font-mono px-2.5 py-0.5 rounded-full border',
              runState === 'running' && 'text-[#D97757] border-[#D97757]/40 bg-[#332018] animate-pulse',
              runState === 'approval' && 'text-[#E5A93C] border-[#E5A93C]/40 bg-[#2C2210]',
              runState === 'done' && 'text-[#6BBF8E] border-[#6BBF8E]/40 bg-[#17281E]',
              runState === 'idle' && 'text-[#787268] border-[#33312D] bg-[#141413]',
            )}>
              {runState === 'running' ? '... Executing task' : runState === 'approval' ? 'Paused · Approval' : runState === 'done' ? 'Synced to Phone ✓' : 'Ready to record'}
            </span>
          </div>

          {/* Desktop Window Frame */}
          <div className="flex-1 relative rounded-2xl border border-[#45423C] bg-[#0E0E0D] shadow-2xl overflow-hidden flex flex-col">
            {/* macOS / RDP Window Chrome */}
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#33312D] bg-[#1C1B19]">
              <div className="flex items-center gap-2">
                <div className="flex gap-1.5">
                  <span className="w-3 h-3 rounded-full bg-[#E06C65]/80" />
                  <span className="w-3 h-3 rounded-full bg-[#E5A93C]/80" />
                  <span className="w-3 h-3 rounded-full bg-[#6BBF8E]/80" />
                </div>
                <span className="text-xs font-mono text-[#B0AAA0] ml-2">{scene.windowTitle}</span>
              </div>
              <span className="text-[10px] font-mono text-[#787268]">RDP · 1920x1080 · {scene.profile}</span>
            </div>

            {/* Scene Body */}
            <div className="flex-1 p-5 overflow-y-auto relative">
              {/* ─── SCENE 1: CALENDAR & REMINDERS ─── */}
              {sceneId === 'calendar' && (
                <div className="grid grid-cols-12 gap-4 h-full">
                  {/* Calendar week grid */}
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
                          <span className={cn(
                            'text-xs font-mono',
                            ev.urgent ? 'text-[#E5A93C]' : 'text-[#B0AAA0]',
                          )}>
                            {ev.time}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Reminders Drawer */}
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
                        ✓ Extracted 4 deadlines & synced summary back to Mobile Chat.
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* ─── SCENE 2: WEBSITE APPLICATION FORM ─── */}
              {sceneId === 'form' && (
                <div className="max-w-xl mx-auto rounded-xl border border-[#33312D] bg-[#141413] p-5 space-y-4">
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

                  <div className="space-y-3">
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
                          'w-full rounded-lg border px-3 py-2 text-xs font-mono min-h-[34px] flex items-center transition-colors',
                          field.val
                            ? 'border-[#D97757]/60 bg-[#1C1B19] text-[#F4F1EA]'
                            : 'border-[#33312D] bg-[#0E0E0D] text-[#4A463F]',
                        )}>
                          {field.val || 'Waiting for agent input…'}
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="pt-2 flex items-center justify-between gap-3">
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
                  {/* Editor */}
                  <div className="rounded-xl border border-[#33312D] bg-[#141413] p-4 flex-1">
                    <div className="flex items-center justify-between mb-2 pb-2 border-b border-[#33312D]">
                      <span className="text-xs font-mono text-[#D97757]">src/agent/gateway.py</span>
                      <span className="text-[10px] font-mono text-[#787268]">Python 3.12 · UTF-8</span>
                    </div>
                    <pre className="text-xs font-mono text-[#F4F1EA] leading-relaxed whitespace-pre-wrap">
                      {codeSnippet}
                    </pre>
                  </div>

                  {/* Terminal */}
                  <div className="rounded-xl border border-[#33312D] bg-[#0B0B0A] p-4 h-44 overflow-y-auto font-mono text-xs space-y-1">
                    <div className="text-[10px] text-[#787268] mb-1">TERMINAL · bash</div>
                    {termLines.map((line, i) => (
                      <p
                        key={i}
                        className={cn(
                          line.includes('passed') ? 'text-[#6BBF8E] font-semibold' : 'text-[#B0AAA0]',
                        )}
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

              {/* ─── Animated AI Agent Cursor ─── */}
              {runState !== 'idle' && (
                <div
                  className="pointer-events-none absolute z-30 transition-all duration-700 ease-out"
                  style={{ left: `${cursor.x}%`, top: `${cursor.y}%` }}
                >
                  <div className="relative">
                    {/* Pointer triangle */}
                    <svg width="20" height="20" viewBox="0 0 24 24" className="drop-shadow-lg">
                      <path
                        d="M4 2L20 11L12 13.5L9.5 21L4 2Z"
                        fill="#D97757"
                        stroke="#141413"
                        strokeWidth="1.8"
                      />
                    </svg>
                    {cursor.clicking && (
                      <span className="absolute -top-1 -left-1 w-6 h-6 rounded-full border-2 border-[#D97757] animate-ping opacity-75" />
                    )}
                    <div className="ml-4 -mt-1 px-2 py-0.5 rounded-md bg-[#D97757] text-[#141413] text-[10px] font-bold whitespace-nowrap shadow">
                      {cursor.label}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* RIGHT: MCP Gateway Side Panel */}
        <div className="w-[340px] shrink-0 border-l border-[#33312D] bg-[#1C1B19] flex flex-col overflow-hidden">
          <div className="p-4 border-b border-[#33312D]">
            <p className="text-xs font-bold text-[#F4F1EA]">MCP Control Plane</p>
            <p className="text-[11px] text-[#787268] mt-0.5">Live policy pipeline & tool audit</p>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-5">
            {/* Pipeline */}
            <div>
              <p className="text-[10px] font-bold uppercase tracking-widest text-[#787268] mb-2">
                Gateway Pipeline
              </p>
              <div className="space-y-1.5">
                {PIPELINE_STEPS.map((label, idx) => {
                  const passed = idx <= activePipeIdx
                  const waiting = runState === 'approval' && idx === 5
                  return (
                    <div
                      key={label}
                      className="flex items-center justify-between text-xs px-2.5 py-1.5 rounded-lg bg-[#141413] border border-[#33312D]"
                    >
                      <span className={passed ? 'text-[#F4F1EA]' : 'text-[#787268]'}>{label}</span>
                      <span className={cn(
                        'text-[10px] font-mono',
                        waiting ? 'text-[#E5A93C]' : passed ? 'text-[#6BBF8E]' : 'text-[#4A463F]',
                      )}>
                        {waiting ? 'WAIT' : passed ? 'PASS' : '—'}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Live Tool Calls */}
            <div>
              <p className="text-[10px] font-bold uppercase tracking-widest text-[#787268] mb-2">
                Executed MCP Calls ({feed.length})
              </p>
              {feed.length === 0 ? (
                <div className="rounded-xl border border-dashed border-[#33312D] p-4 text-center text-xs text-[#787268]">
                  Click <strong className="text-[#D97757]">▶ Run Live Task</strong> to watch the agent execute
                </div>
              ) : (
                <div className="space-y-2">
                  {feed.map(item => (
                    <div
                      key={item.id}
                      className="rounded-xl border border-[#33312D] bg-[#141413] p-2.5 space-y-1"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-mono font-semibold text-[#F4F1EA]">{item.tool}</span>
                        <span className={cn(
                          'text-[10px] font-mono px-1.5 py-0.5 rounded',
                          item.decision === 'allow' && 'bg-[#17281E] text-[#6BBF8E]',
                          item.decision === 'ask' && 'bg-[#2C2210] text-[#E5A93C]',
                          item.decision === 'block' && 'bg-[#2D1817] text-[#E06C65]',
                        )}>
                          {item.decision}
                        </span>
                      </div>
                      <p className="text-[11px] text-[#B0AAA0]">{item.detail}</p>
                      <p className="text-[10px] font-mono text-[#787268] truncate">{item.args}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
