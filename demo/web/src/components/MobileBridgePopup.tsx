import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { cn } from '../lib/utils'

interface DemoScenario {
  id: string
  label: string
  prompt: string
  windowTitle: string
  needsApproval?: boolean
  steps: Array<{ tool: string; detail: string; screenLines: string[] }>
  reply: string
}

const SCENARIOS: DemoScenario[] = [
  {
    id: 'calendar',
    label: 'Calendar & Deadlines',
    prompt: 'Open my calendar, browse through my reminders and give me deadlines',
    windowTitle: 'Remote Desktop — Calendar & Reminders',
    steps: [
      {
        tool: 'calendar.open',
        detail: 'Opening Calendar app on laptop (Nov 26 - Dec 03)',
        screenLines: [
          '> Launching Calendar on poorn-workstation...',
          '  Scanning week: Nov 26 - Dec 03',
          '  Loaded 6 calendar events',
        ],
      },
      {
        tool: 'reminders.scan',
        detail: 'Browsing Reminders & sorting urgent deadlines',
        screenLines: [
          '> Reading Reminders lists [College, Project, Personal]...',
          '  [!] Today 16:30  Project meeting with Shreya',
          '  [!] Today 19:00  Send slides to Prof. Nair',
          '  [ ] Nov 27 11:00 Dentist appt — Indiranagar',
          '  [ ] Nov 28 18:00 Debug auth module with Aarav',
        ],
      },
      {
        tool: 'bridge.sync_mobile',
        detail: 'Sending formatted deadline summary back to Phone',
        screenLines: [
          '> Conflict check: 0 overlaps found',
          '> Encrypting payload -> Mobile Bridge...',
          '[OK] Delivered deadlines to Poorn\'s Phone',
        ],
      },
    ],
    reply: 'Sent 4 upcoming deadlines to Poorn\'s Phone. No conflicts detected.',
  },
  {
    id: 'form',
    label: 'Fill Application Form',
    prompt: 'Go to this specific website and fill an application form for me',
    windowTitle: 'Remote Desktop — Chromium (portal.example.org/apply)',
    needsApproval: true,
    steps: [
      {
        tool: 'browser.navigate',
        detail: 'Opening portal.example.org/apply in desktop browser',
        screenLines: [
          '> Navigating: https://portal.example.org/apply',
          '  200 OK · Loaded "2026 Application Form"',
          '  Detected 6 form inputs + submit button',
        ],
      },
      {
        tool: 'browser.fill_fields',
        detail: 'Auto-filling Name, Email, Project & GitHub repo',
        screenLines: [
          '  [OK] Full Name: Poorn',
          '  [OK] Email:     poorn@overwatch.dev',
          '  [OK] Project:   Overwatch MCP Control Plane',
          '  [OK] Repo:      github.com/poorn/overwatch',
        ],
      },
      {
        tool: 'browser.submit_form',
        detail: 'Paused before Submit — awaiting human permission',
        screenLines: [
          '  [PAUSE] Policy gate: browser.submit_form (sensitive)',
          '  Waiting for approval on Laptop / Phone...',
        ],
      },
    ],
    reply: 'Form submitted after approval. Confirmation receipt synced to Phone.',
  },
  {
    id: 'python',
    label: 'Resume Python Project',
    prompt: 'Resume building my project in Python',
    windowTitle: 'Remote Desktop — Terminal (/workspace/python-project)',
    steps: [
      {
        tool: 'files.scan_workspace',
        detail: 'Checking git status and unfinished Python files',
        screenLines: [
          '$ git status -s',
          ' M src/agent/gateway.py',
          ' M tests/test_policy.py',
          '  Last commit: "wip: policy evaluator"',
        ],
      },
      {
        tool: 'terminal.run',
        detail: 'Running pytest test suite in Python 3.12 venv',
        screenLines: [
          '$ pytest tests/ -q',
          '.............. [100%]',
          '14 passed in 0.84s',
        ],
      },
      {
        tool: 'files.edit',
        detail: 'Resumed gateway.py retry guard & synced status to Phone',
        screenLines: [
          '> Patched src/agent/gateway.py (+12 lines)',
          '> Re-ran smoke check: [OK] exit 0',
          '[OK] Synced build status to Poorn\'s Phone',
        ],
      },
    ],
    reply: 'Resumed Python build (14 tests passed). Status sent to Phone.',
  },
  {
    id: 'research',
    label: 'Research & Gmail Draft',
    prompt: 'Research MCP sandboxing papers, summarize findings and email my mentor',
    windowTitle: 'Remote Desktop — Browser + Gmail',
    steps: [
      {
        tool: 'browser.search',
        detail: 'Searching arXiv & MCP spec on sandboxing',
        screenLines: [
          '> Searching arXiv & modelcontextprotocol.io...',
          '  Parsed 3 key security takeaways',
        ],
      },
      {
        tool: 'gmail.create_draft',
        detail: 'Drafted email to mentor@university.edu',
        screenLines: [
          '> Composing Gmail draft (dr_8841)...',
          '[BLOCKED] gmail.send denied in Research mode — draft saved',
        ],
      },
    ],
    reply: 'Saved Gmail draft safely (auto-send blocked). Ready for review.',
  },
]

export function MobileBridgePopup() {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [expandedDesktop, setExpandedDesktop] = useState(false)
  const [active, setActive] = useState<DemoScenario | null>(null)
  const [stepIdx, setStepIdx] = useState(-1)
  const [status, setStatus] = useState<'idle' | 'running' | 'approval' | 'done'>('idle')

  async function runScenario(sc: DemoScenario) {
    setActive(sc)
    setOpen(true)
    setStatus('running')
    setStepIdx(0)

    for (let i = 0; i < sc.steps.length; i++) {
      setStepIdx(i)
      // 3.5s realistic gap per step so user can narrate live
      await new Promise(r => setTimeout(r, 3500))
    }

    if (sc.needsApproval) {
      setStatus('approval')
    } else {
      setStatus('done')
    }
  }

  function handleApprove() {
    setStatus('done')
  }

  const currentStep = active && stepIdx >= 0 ? active.steps[Math.min(stepIdx, active.steps.length - 1)] : null

  return (
    <>
      {/* Optional Expanded Remote Desktop Agent Window */}
      {expandedDesktop && active && (
        <div className="fixed inset-0 z-40 bg-black/70 backdrop-blur-xs flex items-center justify-center p-6">
          <div className="w-full max-w-2xl rounded-2xl border border-[#45423C] bg-[#141413] shadow-2xl overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-[#33312D] bg-[#1C1B19]">
              <div className="flex items-center gap-2">
                <div className="flex gap-1.5">
                  <span className="w-3 h-3 rounded-full bg-[#E06C65]/80" />
                  <span className="w-3 h-3 rounded-full bg-[#E5A93C]/80" />
                  <span className="w-3 h-3 rounded-full bg-[#6BBF8E]/80" />
                </div>
                <span className="text-xs font-mono text-[#B0AAA0] ml-2">{active.windowTitle}</span>
              </div>
              <button
                onClick={() => setExpandedDesktop(false)}
                className="text-xs text-[#787268] hover:text-[#F4F1EA] px-2 py-1"
              >
                Minimize
              </button>
            </div>

            <div className="p-5 space-y-4">
              <div className="flex items-center justify-between rounded-xl border border-[#33312D] bg-[#1C1B19] px-3.5 py-2.5">
                <div className="text-xs text-[#B0AAA0]">
                  <span className="text-[#D97757] font-semibold">Mobile Command: </span>
                  “{active.prompt}”
                </div>
                {status === 'running' && (
                  <span className="text-[11px] font-mono text-[#D97757] animate-pulse">
                    ... Executing task
                  </span>
                )}
              </div>

              {/* Simulated Live Desktop Screen */}
              <div className="rounded-xl border border-[#33312D] bg-[#0D0D0C] p-4 font-mono text-xs min-h-[160px] space-y-1.5">
                {currentStep?.screenLines.map((line, i) => (
                  <p
                    key={i}
                    className={cn(
                      line.startsWith('[OK]') || line.includes('passed')
                        ? 'text-[#6BBF8E]'
                        : line.includes('[!]') || line.includes('[PAUSE]')
                          ? 'text-[#E5A93C]'
                          : 'text-[#F4F1EA]/90',
                    )}
                  >
                    {line}
                  </p>
                ))}
                {status === 'running' && (
                  <span className="inline-block w-2 h-3.5 bg-[#D97757] animate-pulse mt-1" />
                )}
              </div>

              {status === 'approval' && (
                <div className="rounded-xl border border-[#E5A93C]/50 bg-[#2C2210] p-3.5 flex items-center justify-between gap-4">
                  <p className="text-xs text-[#E5A93C] font-medium">
                    Action paused by MCP Gateway — approve submission?
                  </p>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setStatus('idle')}
                      className="text-xs px-3 py-1.5 rounded-lg border border-[#33312D] bg-[#1C1B19] text-[#B0AAA0]"
                    >
                      Deny
                    </button>
                    <button
                      onClick={handleApprove}
                      className="text-xs px-3.5 py-1.5 rounded-lg bg-[#D97757] text-[#141413] font-semibold"
                    >
                      Approve & Sync Back
                    </button>
                  </div>
                </div>
              )}

              {status === 'done' && (
                <div className="rounded-xl border border-[#6BBF8E]/40 bg-[#17281E] p-3 text-xs text-[#6BBF8E]">
                  ✓ {active.reply}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Floating Bridge Control */}
      <div className="fixed bottom-5 right-5 z-50 flex flex-col items-end gap-2">
        {open && (
          <div className="w-96 rounded-2xl border border-[#45423C] bg-[#1C1B19] shadow-2xl overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-[#33312D] bg-[#242320]">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-[#6BBF8E] animate-pulse" />
                <span className="text-xs font-semibold text-[#F4F1EA]">
                  Mobile → Remote Desktop Bridge
                </span>
              </div>
              <button
                onClick={() => setOpen(false)}
                className="text-xs text-[#787268] hover:text-[#F4F1EA] px-1.5 py-0.5"
              >
                ✕
              </button>
            </div>

            <div className="p-4 space-y-3">
              {/* Scenario picker */}
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-[#787268] mb-2">
                  Trigger Live Laptop Agent View
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {SCENARIOS.map(sc => (
                    <button
                      key={sc.id}
                      onClick={() => runScenario(sc)}
                      className={cn(
                        'text-xs px-2.5 py-1.5 rounded-lg border transition-all',
                        active?.id === sc.id
                          ? 'border-[#D97757] bg-[#332018] text-[#D97757] font-medium'
                          : 'border-[#33312D] bg-[#141413] text-[#B0AAA0] hover:border-[#45423C]',
                      )}
                    >
                      {sc.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Active Execution */}
              {active && (
                <div className="rounded-xl border border-[#33312D] bg-[#141413] p-3 space-y-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] font-mono text-[#D97757]">
                      {status === 'running' ? '... Executing task' : status === 'approval' ? 'Paused (Approval)' : 'Completed'}
                    </span>
                    <button
                      onClick={() => {
                        setOpen(false)
                        navigate(`/live-scene?scene=${active.id}`)
                      }}
                      className="text-[11px] text-[#D97757] hover:underline font-semibold"
                    >
                      Full Live Scene →
                    </button>
                  </div>

                  <div className="text-xs text-[#B0AAA0]">
                    <span className="text-[#D97757] font-semibold">From Phone: </span>
                    “{active.prompt}”
                  </div>

                  <div className="space-y-1.5">
                    {active.steps.map((st, idx) => {
                      const reached = idx <= stepIdx
                      return (
                        <div
                          key={st.tool}
                          className={cn(
                            'flex items-start gap-2 text-xs font-mono transition-opacity',
                            reached ? 'opacity-100' : 'opacity-25',
                          )}
                        >
                          <span className={reached ? 'text-[#6BBF8E]' : 'text-[#787268]'}>
                            {reached ? '✓' : '·'}
                          </span>
                          <div>
                            <span className="text-[#F4F1EA]">{st.tool}</span>
                            <p className="text-[11px] text-[#787268] font-sans">{st.detail}</p>
                          </div>
                        </div>
                      )
                    })}
                  </div>

                  {status === 'approval' && (
                    <div className="rounded-lg border border-[#E5A93C]/40 bg-[#2C2210] p-2.5 space-y-2">
                      <p className="text-xs text-[#E5A93C] font-medium">
                        Permission required before clicking Submit
                      </p>
                      <div className="flex gap-2">
                        <button
                          onClick={() => setStatus('idle')}
                          className="flex-1 text-xs py-1.5 rounded-md border border-[#33312D] bg-[#1C1B19] text-[#B0AAA0]"
                        >
                          Deny
                        </button>
                        <button
                          onClick={handleApprove}
                          className="flex-2 text-xs py-1.5 px-3 rounded-md bg-[#D97757] text-[#141413] font-semibold"
                        >
                          Approve & Sync to Phone
                        </button>
                      </div>
                    </div>
                  )}

                  {status === 'done' && (
                    <div className="rounded-lg border border-[#6BBF8E]/40 bg-[#17281E] p-2.5 text-xs text-[#6BBF8E]">
                      ✓ {active.reply}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Floating trigger pill */}
        <button
          onClick={() => setOpen(o => !o)}
          className="flex items-center gap-2 rounded-full border border-[#45423C] bg-[#1C1B19] hover:border-[#D97757] px-3.5 py-2 shadow-lg transition-all"
        >
          <span className="w-2 h-2 rounded-full bg-[#6BBF8E] animate-pulse" />
          <span className="text-xs font-medium text-[#F4F1EA]">
            Phone Connected
          </span>
          <span className="text-[11px] text-[#D97757] font-mono">Demo Bridge</span>
        </button>
      </div>
    </>
  )
}
