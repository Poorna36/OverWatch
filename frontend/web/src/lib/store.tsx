import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { DEMO_EVENTS, seedSessions, type ProfileId, type Session, type SessionState, type ToolCall } from "./mock";

export type Tier = "video" | "screenshot" | "text";
export interface Draft { prompt: string; profile: ProfileId; dryRun: boolean }
export interface Settings { connector: boolean; serverUrl: string; token: string }
const STORAGE_KEY = "overwatch.demo.v1";
const defaultSettings: Settings = { connector: false, serverUrl: "", token: "" };
function load() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (value?.version === 1) return value;
  } catch { /* Ignore malformed demo storage. */ }
  return {};
}

interface Store {
  sessions: Session[]; tier: Tier; draft: Draft | null; settings: Settings;
  setTier: (t: Tier) => void; setState: (id: string, s: SessionState) => void;
  decide: (id: string, seq: number, approve: boolean, expired?: boolean) => void;
  appendDemo: (id: string) => void;
  setDraft: (draft: Draft) => void;
  updateSettings: (settings: Partial<Settings>) => void;
  createSession: (title: string, profile: ProfileId, steps?: { tool: string; desc: string; risk: ToolCall["risk"]; reversible: boolean }[], dryRun?: boolean) => string;
  clearAll: () => void; reset: () => void;
  restoreSessions: (sessions: Session[]) => void;
}
const Ctx = createContext<Store | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [initial] = useState(load);
  const [sessions, setSessions] = useState<Session[]>(initial.sessions ?? seedSessions());
  const [tier, setTier] = useState<Tier>(initial.tier ?? "screenshot");
  const [draft, setDraftState] = useState<Draft | null>(initial.draft ?? null);
  const [settings, setSettings] = useState<Settings>({ ...defaultSettings, ...initial.settings });
  const [demoIdx, setDemoIdx] = useState(0);
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, sessions, tier, draft, settings }));
  }, [sessions, tier, draft, settings]);

  const patch = (id: string, fn: (s: Session) => Session) => setSessions((all) => all.map((s) => s.id === id ? fn(s) : s));
  const store: Store = {
    sessions, tier, draft, settings, setTier,
    setDraft: (value) => setDraftState(value),
    updateSettings: (value) => setSettings((s) => ({ ...s, ...value })),
    setState: (id, state) => patch(id, (s) => ({ ...s, state, calls: state === "ENDED" ? s.calls.map((c) => c.status === "pending" ? { ...c, status: "denied", reason: "SESSION_ENDED", latencyMs: 0 } : c) : s.calls })),
    decide: (id, seq, approve, expired) => patch(id, (s) => ({ ...s, state: s.state === "AWAITING" ? "ACTIVE" : s.state, calls: s.calls.map((c) => c.seq === seq ? { ...c, status: approve ? "approved" : "denied", reason: expired ? "APPROVAL_EXPIRED" : approve ? "USER_APPROVED" : "USER_DENIED", latencyMs: approve ? 1840 : 0 } : c) })),
    appendDemo: (id) => {
      const ev = DEMO_EVENTS[demoIdx % DEMO_EVENTS.length]!; setDemoIdx((i) => i + 1);
      patch(id, (s) => { const call = { ...ev, seq: s.calls.length + 1, ts: new Date().toISOString() }; return { ...s, state: call.decision === "ask" ? "AWAITING" : s.state, budget: { ...s.budget, used: Math.min(s.budget.limit, +(s.budget.used + 0.04).toFixed(2)) }, calls: [...s.calls, call] }; });
    },
    createSession: (title, profile, steps = [], dryRun = false) => {
      const id = "ses_" + Math.random().toString(16).slice(2, 6);
      const calls: ToolCall[] = steps.map((step, i) => ({ seq: i + 1, ts: new Date().toISOString(), tool: step.tool, args: { detail: step.desc }, decision: step.reversible ? "allow" : "ask", status: step.reversible ? "done" : "pending", reason: step.reversible ? "POLICY_ALLOW" : "EXTERNAL_TRANSFER", latencyMs: step.reversible ? 150 + i * 80 : null, risk: step.risk, reversible: step.reversible }));
      setSessions((all) => [{ id, title: `${dryRun ? "[Dry run] " : ""}${title}`, profile, state: calls.some((c) => c.status === "pending") ? "AWAITING" : "ACTIVE", startedAt: new Date().toISOString(), budget: { used: 0, limit: 2 }, calls }, ...all]);
      return id;
    },
    clearAll: () => setSessions([]),
    restoreSessions: (value) => setSessions(value),
    reset: () => { setSessions(seedSessions()); setTier("screenshot"); setDraftState(null); setSettings(defaultSettings); },
  };
  return <Ctx.Provider value={store}>{children}</Ctx.Provider>;
}

export function useStore() { const s = useContext(Ctx); if (!s) throw new Error("StoreProvider missing"); return s; }
