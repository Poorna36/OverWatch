import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Button, RiskBadge } from '../components/ui'
import { cn } from '../lib/utils'

interface PlanStep {
  id: number
  tool: string
  riskTier: 'safe' | 'sensitive' | 'critical'
  reversible: boolean
  description: string
  removed: boolean
}

const INITIAL_PLAN: PlanStep[] = [
  { id: 1, tool: 'files.write', riskTier: 'safe', reversible: true, description: 'Create /workspace/fetch_title.py', removed: false },
  { id: 2, tool: 'terminal.run', riskTier: 'safe', reversible: false, description: 'pip install requests', removed: false },
  { id: 3, tool: 'terminal.run', riskTier: 'safe', reversible: false, description: 'python fetch_title.py <url>', removed: false },
  { id: 4, tool: 'files.upload', riskTier: 'sensitive', reversible: false, description: 'Upload result to drive', removed: false },
]

const BUDGET_EST = '$0.09'
const BUDGET_MAX = '$0.50'

export function PlanPreviewPage() {
  const navigate = useNavigate()
  const [steps, setSteps] = useState<PlanStep[]>(INITIAL_PLAN)
  const [approving, setApproving] = useState(false)

  const active = steps.filter(s => !s.removed)
  const hasAsk = active.some(s => s.riskTier !== 'safe')

  function toggle(id: number) {
    setSteps(ss => ss.map(s => s.id === id ? { ...s, removed: !s.removed } : s))
  }

  async function handleApprove() {
    setApproving(true)
    await new Promise(r => setTimeout(r, 900))
    navigate('/session/sess_abc123')
  }

  async function handleDryRun() {
    setApproving(true)
    await new Promise(r => setTimeout(r, 900))
    navigate('/session/sess_abc123')
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-2xl mx-auto px-6 py-10 space-y-6">

        {/* Header */}
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-medium text-indigo-400 bg-indigo-400/10 border border-indigo-400/20 rounded px-2 py-0.5">STRETCH</span>
            <span className="text-xs text-zinc-600">Plan preview</span>
          </div>
          <h1 className="text-2xl font-bold text-zinc-100">Review plan</h1>
          <p className="text-sm text-zinc-500 mt-1">
            The agent will execute these steps in order. Remove steps or switch to dry-run.
          </p>
        </div>

        {/* Task */}
        <Card className="bg-zinc-950">
          <p className="text-xs text-zinc-600 mb-1 font-mono">Task</p>
          <p className="text-sm text-zinc-300">
            Create a Python script that fetches a page title and runs it
          </p>
        </Card>

        {/* Budget estimate */}
        <div className="flex gap-3">
          {[
            { label: 'Est. cost', value: BUDGET_EST, sub: `of ${BUDGET_MAX} limit` },
            { label: 'Steps', value: active.length.toString(), sub: `${steps.length - active.length} removed` },
            { label: 'Sensitive', value: active.filter(s => s.riskTier !== 'safe').length.toString(), sub: 'will ask' },
          ].map(stat => (
            <Card key={stat.label} className="flex-1 text-center py-4">
              <p className="text-xl font-bold font-mono text-zinc-100">{stat.value}</p>
              <p className="text-xs text-zinc-500 mt-0.5">{stat.label}</p>
              <p className="text-[10px] text-zinc-700 mt-0.5">{stat.sub}</p>
            </Card>
          ))}
        </div>

        {/* Plan steps */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-widest">Steps</h2>
            <p className="text-xs text-zinc-700">Click a step to remove it</p>
          </div>
          {steps.map((step, i) => (
            <button
              key={step.id}
              onClick={() => toggle(step.id)}
              className={cn(
                'w-full text-left rounded-xl border p-3.5 transition-all group',
                step.removed
                  ? 'border-zinc-800/40 bg-zinc-900/30 opacity-40'
                  : 'border-zinc-800 bg-zinc-900 hover:border-zinc-700',
              )}
            >
              <div className="flex items-center gap-3">
                {/* Step number */}
                <div className={cn(
                  'w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold shrink-0 transition-all',
                  step.removed
                    ? 'bg-zinc-800 text-zinc-700 line-through'
                    : 'bg-indigo-600/20 text-indigo-400',
                )}>
                  {step.removed ? '✕' : i + 1}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className={cn(
                      'text-xs font-mono font-medium',
                      step.removed ? 'text-zinc-700 line-through' : 'text-zinc-200',
                    )}>
                      {step.tool}
                    </span>
                  </div>
                  <p className={cn(
                    'text-xs',
                    step.removed ? 'text-zinc-700' : 'text-zinc-500',
                  )}>
                    {step.description}
                  </p>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  <RiskBadge tier={step.riskTier} />
                  <span className={cn(
                    'text-[10px] px-1.5 py-0.5 rounded border',
                    step.reversible
                      ? 'text-zinc-600 border-zinc-800'
                      : 'text-amber-600 border-amber-900/40',
                  )}>
                    {step.reversible ? 'reversible' : 'irreversible'}
                  </span>
                </div>
              </div>
            </button>
          ))}
        </div>

        {/* Sensitive warning */}
        {hasAsk && (
          <Card className="border-amber-500/20 bg-amber-500/5">
            <div className="flex items-start gap-2">
              <span className="text-amber-400 mt-0.5">⚠</span>
              <div>
                <p className="text-sm font-medium text-amber-400">Sensitive actions require approval</p>
                <p className="text-xs text-amber-500/60 mt-0.5">
                  Steps marked <em>sensitive</em> will pause and show an approval card on your phone before executing.
                </p>
              </div>
            </div>
          </Card>
        )}

        {/* Actions */}
        <div className="flex gap-3">
          <Button
            variant="outline"
            size="md"
            onClick={() => navigate('/new')}
            className="flex-1"
          >
            ← Back
          </Button>
          <Button
            variant="outline"
            size="md"
            onClick={handleDryRun}
            loading={approving}
            className="flex-1"
          >
            Dry-run
          </Button>
          <Button
            size="md"
            onClick={handleApprove}
            loading={approving}
            disabled={active.length === 0}
            className="flex-1"
          >
            Approve & start
          </Button>
        </div>

        <p className="text-xs text-zinc-700 text-center">
          Calls outside this plan will be escalated to <em>ask</em> automatically.
        </p>
      </div>
    </div>
  )
}
