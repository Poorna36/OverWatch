import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PROFILES } from '../lib/mock-data'
import { Button, Card } from '../components/ui'
import { cn } from '../lib/utils'

const EXAMPLE_TASKS = [
  'Create a Python script that fetches a page title and runs it',
  'Research recent papers on transformer architectures and summarize',
  'Set up a TypeScript monorepo with ESLint and Prettier',
  'Draft a reply to my latest email about the project deadline',
]

export function NewTaskPage() {
  const navigate = useNavigate()
  const [task, setTask] = useState('')
  const [profile, setProfile] = useState('coding')
  const [dryRun, setDryRun] = useState(false)
  const [starting, setStarting] = useState(false)

  async function handleStart() {
    if (!task.trim()) return
    setStarting(true)
    // Simulate a brief delay then navigate to the demo session
    await new Promise(r => setTimeout(r, 1200))
    navigate('/session/sess_abc123')
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-2xl mx-auto px-6 py-10 space-y-6">

        {/* Header */}
        <div>
          <h1 className="text-2xl font-bold text-zinc-100">New task</h1>
          <p className="text-sm text-zinc-500 mt-1">
            Describe what the agent should do. A sandboxed workspace will spin up automatically.
          </p>
        </div>

        {/* Task input */}
        <div className="space-y-2">
          <label className="text-xs font-medium text-zinc-500 uppercase tracking-widest">
            Task description
          </label>
          <textarea
            value={task}
            onChange={e => setTask(e.target.value)}
            placeholder="What should the agent do?"
            rows={4}
            className="w-full rounded-xl bg-zinc-900 border border-zinc-800 px-4 py-3 text-sm text-zinc-200 placeholder-zinc-700 resize-none focus:outline-none focus:border-indigo-500/60 focus:ring-1 focus:ring-indigo-500/30 transition-all"
          />
        </div>

        {/* Example prompts */}
        <div>
          <p className="text-xs text-zinc-600 mb-2">Try an example</p>
          <div className="flex flex-wrap gap-2">
            {EXAMPLE_TASKS.map(t => (
              <button
                key={t}
                onClick={() => setTask(t)}
                className="text-xs px-3 py-1.5 rounded-lg border border-zinc-800 text-zinc-500 hover:text-zinc-300 hover:border-zinc-700 hover:bg-zinc-800/60 transition-all truncate max-w-xs"
              >
                {t.length > 50 ? t.slice(0, 50) + '…' : t}
              </button>
            ))}
          </div>
        </div>

        {/* Profile picker */}
        <div className="space-y-2">
          <label className="text-xs font-medium text-zinc-500 uppercase tracking-widest">
            Profile
          </label>
          <div className="grid grid-cols-3 gap-2">
            {PROFILES.map(p => (
              <button
                key={p.name}
                onClick={() => setProfile(p.name)}
                className={cn(
                  'rounded-xl border p-3 text-left transition-all',
                  profile === p.name
                    ? 'border-indigo-500/50 bg-indigo-600/10'
                    : 'border-zinc-800 hover:border-zinc-700 hover:bg-zinc-800/60',
                )}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className={cn('text-sm font-medium', profile === p.name ? 'text-indigo-400' : 'text-zinc-300')}>
                    {p.name}
                  </span>
                  {profile === p.name && (
                    <div className="w-1.5 h-1.5 rounded-full bg-indigo-400" />
                  )}
                </div>
                <div className="flex flex-wrap gap-1">
                  {p.tools.map(t => (
                    <span key={t} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-500">
                      {t}
                    </span>
                  ))}
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Options */}
        <Card className="flex items-center justify-between py-3">
          <div>
            <p className="text-sm font-medium text-zinc-300">Dry-run mode</p>
            <p className="text-xs text-zinc-600 mt-0.5">Reads run normally; writes are recorded but not applied</p>
          </div>
          <button
            onClick={() => setDryRun(d => !d)}
            className={cn(
              'relative w-10 h-6 rounded-full transition-colors border',
              dryRun ? 'bg-indigo-600 border-indigo-500' : 'bg-zinc-800 border-zinc-700',
            )}
          >
            <span className={cn(
              'absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all',
              dryRun ? 'left-[18px]' : 'left-0.5',
            )} />
          </button>
        </Card>

        {/* Start button */}
        <Button
          size="lg"
          onClick={handleStart}
          loading={starting}
          disabled={!task.trim()}
          className="w-full"
        >
          {starting ? 'Starting session…' : 'Start session'}
        </Button>

        {/* Fine print */}
        <p className="text-xs text-zinc-700 text-center">
          A sandboxed container will be created. Every tool call passes through the MCP gateway.
        </p>
      </div>
    </div>
  )
}
