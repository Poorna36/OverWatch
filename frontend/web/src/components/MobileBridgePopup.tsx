import { useState } from 'react'
import { cn } from '../lib/utils'

interface DemoScenario {
  id: string
  label: string
  prompt: string
  needsApproval?: boolean
  steps: Array<{ tool: string; detail: string }>
  reply: string
}

const SCENARIOS: DemoScenario[] = [
  {
    id: 'calendar',
    label: 'Calendar & Deadlines',
    prompt: 'Open my calendar, browse through my reminders and give me deadlines',
    steps: [
      { tool: 'calendar.open', detail: 'Reading Nov 26 – Dec 03 events on Desktop' },
      { tool: 'reminders.scan', detail: 'Found 4 due reminders (Today 5:00 PM urgent)' },
      { tool: 'bridge.sync_mobile', detail: 'Sending deadline summary back to Phone' },
    ],
    reply: 'Sent 4 upcoming deadlines to Poorn\'s Phone. No conflicts detected.',
  },
  {
    id: 'form',
    label: 'Fill Application Form',
    prompt: 'Go to this specific website and fill an application form for me',
    needsApproval: true,
    steps: [
      { tool: 'browser.navigate', detail: 'Opened https://portal.example.org/apply' },
      { tool: 'browser.fill_fields', detail: 'Filled 6 fields from user profile' },
      { tool: 'browser.submit_form', detail: 'Paused — waiting for approval before Submit' },
    ],
    reply: 'Form submitted after approval. Confirmation receipt synced to Phone.',
  },
  {
    id: 'python',
    label: 'Resume Python Project',
    prompt: 'Resume building my project in Python',
    steps: [
      { tool: 'files.scan_workspace', detail: 'Found WIP in src/agent/gateway.py' },
      { tool: 'terminal.run', detail: 'pytest tests/ -q -> 14 passed in 0.84s' },
      { tool: 'files.edit', detail: 'Added async timeout guard & synced status' },
    ],
    reply: 'Resumed Python build (14 tests passed). Status sent to Phone.',
  },
]

export function MobileBridgePopup() {
  const [open, setOpen] = useState(false)
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
      await new Promise(r => setTimeout(r, 950))
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

  return (
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
                Simulate Incoming Mobile Command
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
  )
}
