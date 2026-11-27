import React, { createContext, useContext, useState, type ReactNode } from 'react'
import { DEMO_EVENTS, seedSessions, type ProfileId, type Session, type SessionState } from './mock'

export type Tier = 'video' | 'screenshot' | 'text'

interface Store {
  sessions: Session[]
  tier: Tier
  setTier: (t: Tier) => void
  setState: (id: string, s: SessionState) => void
  decide: (id: string, seq: number, approve: boolean, expired?: boolean) => void
  appendDemo: (id: string) => void
  createSession: (title: string, profile: ProfileId) => string
  clearAll: () => void
  reset: () => void
}

const Ctx = createContext<Store | null>(null)

export function StoreProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<Session[]>(seedSessions)
  const [tier, setTier] = useState<Tier>('screenshot')
  const [demoIdx, setDemoIdx] = useState(0)

  const patch = (id: string, fn: (s: Session) => Session) =>
    setSessions((all) => all.map((s) => (s.id === id ? fn(s) : s)))

  const store: Store = {
    sessions,
    tier,
    setTier,
    setState: (id, state) => patch(id, (s) => ({ ...s, state })),
    decide: (id, seq, approve, expired) =>
      patch(id, (s) => ({
        ...s,
        state: s.state === 'AWAITING' ? 'ACTIVE' : s.state,
        calls: s.calls.map((c) =>
          c.seq === seq
            ? {
                ...c,
                status: approve ? 'approved' : 'denied',
                reason: expired ? 'APPROVAL_EXPIRED' : approve ? 'USER_APPROVED' : 'USER_DENIED',
                latencyMs: approve ? 1840 : 0,
              }
            : c,
        ),
      })),
    appendDemo: (id) => {
      const ev = DEMO_EVENTS[demoIdx % DEMO_EVENTS.length]!
      setDemoIdx((i) => i + 1)
      patch(id, (s) => ({
        ...s,
        budget: { ...s.budget, used: Math.min(s.budget.limit, +(s.budget.used + 0.04).toFixed(2)) },
        calls: [...s.calls, { ...ev, seq: s.calls.length + 1, ts: new Date().toISOString() }],
      }))
    },
    createSession: (title, profile) => {
      const id = 'ses_' + Math.random().toString(16).slice(2, 6)
      setSessions((all) => [
        { id, title, profile, state: 'ACTIVE', startedAt: new Date().toISOString(), budget: { used: 0, limit: 2 }, calls: [] },
        ...all,
      ])
      return id
    },
    clearAll: () => setSessions([]),
    reset: () => setSessions(seedSessions()),
  }
  return <Ctx.Provider value={store}>{children}</Ctx.Provider>
}

export function useStore() {
  const s = useContext(Ctx)
  if (!s) throw new Error('StoreProvider missing')
  return s
}

// Draft task passed from New task -> Plan preview (in-memory only)
export const draft: { prompt: string; profile: ProfileId; dryRun: boolean } = {
  prompt: 'Fix flaky auth tests in api-server',
  profile: 'coding',
  dryRun: false,
}
