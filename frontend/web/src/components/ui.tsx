import { CheckCircle2, ShieldAlert, Ban, Pause, Play, CircleStop, Clock } from "lucide-react";
import type { ReactNode } from "react";
import type { Decision, Risk, SessionState, ToolCall } from "../lib/mock";
import { REASONS } from "../lib/mock";
import { cn } from "../lib/utils";

const decisionMeta: Record<Decision, { label: string; icon: typeof CheckCircle2; cls: string }> = {
  allow: { label: "ALLOW", icon: CheckCircle2, cls: "text-allow bg-allow/10 border-allow/30" },
  ask: { label: "ASK", icon: ShieldAlert, cls: "text-ask bg-ask/10 border-ask/30" },
  block: { label: "BLOCK", icon: Ban, cls: "text-block bg-block/10 border-block/30" },
};

export function DecisionBadge({ d }: { d: Decision }) {
  const m = decisionMeta[d];
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[11px] font-semibold", m.cls)}>
      <m.icon className="size-3.5" aria-hidden /> {m.label}
    </span>
  );
}

export function RiskBadge({ r }: { r: Risk }) {
  const cls = r === "high" ? "text-block" : r === "medium" ? "text-ask" : "text-allow";
  return <span className={cn("font-mono text-[11px] uppercase", cls)}>● {r} risk</span>;
}

export function StateBadge({ s, big }: { s: SessionState; big?: boolean }) {
  const map = {
    ACTIVE: { icon: Play, cls: "text-allow border-allow/40 bg-allow/10" },
    AWAITING: { icon: Clock, cls: "text-ask border-ask/40 bg-ask/10" },
    PAUSED: { icon: Pause, cls: "text-muted-foreground border-border bg-muted" },
    ENDED: { icon: CircleStop, cls: "text-muted-foreground border-border bg-surface" },
  }[s];
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full border font-mono font-semibold tracking-wider", map.cls, big ? "px-3 py-1 text-xs" : "px-2 py-0.5 text-[10px]")}>
      <map.icon className={big ? "size-3.5" : "size-3"} aria-hidden />
      {s === "AWAITING" ? "NEEDS APPROVAL" : s}
    </span>
  );
}

export function Budget({ used, limit }: { used: number; limit: number }) {
  const pct = Math.min(100, (used / limit) * 100);
  const cls = pct > 85 ? "bg-block" : pct > 60 ? "bg-ask" : "bg-primary";
  return (
    <div>
      <div className="mb-1 flex justify-between font-mono text-[11px] text-muted-foreground">
        <span>Budget</span>
        <span>${used.toFixed(2)} / ${limit.toFixed(2)}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
        <div className={cn("h-full rounded-full transition-all", cls)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn("rounded-xl border bg-card p-4", className)}>{children}</div>;
}

export function Btn({ variant = "primary", className, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "approve" | "ghost" }) {
  const v = {
    primary: "bg-primary text-primary-foreground hover:bg-primary/90",
    secondary: "bg-secondary text-secondary-foreground border hover:bg-accent",
    danger: "bg-block/15 text-block border border-block/40 hover:bg-block/25",
    approve: "bg-allow text-background hover:bg-allow/90",
    ghost: "text-muted-foreground hover:bg-muted hover:text-foreground",
  }[variant];
  return <button {...p} className={cn("inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold transition active:scale-[0.98] disabled:opacity-50", v, className)} />;
}

export const fmtTime = (iso: string) => iso.slice(11, 19) + "Z";

export function CallRow({ c, onReview }: { c: ToolCall; onReview?: () => void }) {
  const statusLabel = { done: "executed", pending: "awaiting approval", denied: "denied by user", blocked: "blocked by policy", approved: "approved · executed" }[c.status];
  return (
    <li className="animate-ow-in rounded-lg border bg-surface p-3">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[11px] text-muted-foreground">#{String(c.seq).padStart(2, "0")}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-sm font-semibold">{c.tool}</span>
        <DecisionBadge d={c.decision} />
      </div>
      <dl className="mt-2 space-y-0.5 font-mono text-[12px]">
        {Object.entries(c.args).map(([k, v]) => (
          <div key={k} className="flex gap-2"><dt className="shrink-0 text-muted-foreground">{k}:</dt><dd className="min-w-0 break-all">{v}</dd></div>
        ))}
      </dl>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <span className="font-mono text-foreground/80" title={REASONS[c.reason]}>{c.reason}</span>
        <span>{statusLabel}</span>
        <span className="font-mono">{c.latencyMs === null ? "—" : `${c.latencyMs}ms`}</span>
        <span className="font-mono">{fmtTime(c.ts)}</span>
      </div>
      {c.status === "pending" && onReview && (
        <Btn variant="secondary" className="mt-3 w-full border-ask/40 text-ask" onClick={onReview}><ShieldAlert className="size-4" /> Review request</Btn>
      )}
    </li>
  );
}
