import { Link } from "react-router-dom";
import { Activity, ChevronRight, Plus, RotateCcw, ShieldCheck } from "lucide-react";
import { useStore } from "../lib/store";
import { PROFILES } from "../lib/mock";
import { Btn, Budget, Card, DecisionBadge, StateBadge, fmtTime } from "../components/ui";

export function HomePage() {
  const { sessions, reset } = useStore();
  const live = sessions.filter((s) => s.state !== "ENDED");
  const recent = sessions.filter((s) => s.state === "ENDED");
  const feed = sessions.flatMap((s) => s.calls.map((c) => ({ ...c, sid: s.id }))).sort((a, b) => b.ts.localeCompare(a.ts)).slice(0, 5);
  const pending = sessions.filter((s) => s.state !== "ENDED").reduce((n, s) => n + s.calls.filter((c) => c.status === "pending").length, 0);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight">Sessions</h1>
          <p className="text-sm text-muted-foreground">{live.length} live · {pending} awaiting your approval</p>
        </div>
        <Link to="/new"><Btn className="shrink-0"><Plus className="size-4" /> New task</Btn></Link>
      </div>

      {sessions.length === 0 ? (
        <Card className="py-10 text-center">
          <ShieldCheck className="mx-auto size-10 text-primary" />
          <h2 className="mt-3 font-semibold">No sessions yet</h2>
          <p className="mx-auto mt-1 max-w-xs text-sm text-muted-foreground">Start a task and the agent runs in its own hardened container. Every tool call passes the policy gateway first.</p>
          <div className="mt-5 flex justify-center gap-2">
            <Link to="/new"><Btn><Plus className="size-4" /> Start a task</Btn></Link>
            <Btn variant="secondary" onClick={reset}><RotateCcw className="size-4" /> Restore sessions</Btn>
          </div>
        </Card>
      ) : (
        <>
          <section className="space-y-3">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Active</h2>
            {live.map((s) => <SessionCard key={s.id} id={s.id} />)}
            {live.length === 0 && <p className="text-sm text-muted-foreground">No live sessions.</p>}
          </section>
          {recent.length > 0 && (
            <section className="space-y-3">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Recent</h2>
              {recent.map((s) => <SessionCard key={s.id} id={s.id} />)}
            </section>
          )}
          <section>
            <h2 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground"><Activity className="size-3.5" /> Recent activity</h2>
            <Card className="divide-y p-0">
              {feed.map((c) => (
                <div key={c.sid + c.seq} className="flex items-center gap-3 px-4 py-3">
                  <span className="min-w-0 flex-1 truncate font-mono text-sm">{c.tool} <span className="text-muted-foreground">· {c.reason}</span></span>
                  <span className="hidden font-mono text-[11px] text-muted-foreground sm:inline">{fmtTime(c.ts)}</span>
                  <DecisionBadge d={c.decision} />
                </div>
              ))}
            </Card>
          </section>
        </>
      )}
    </div>
  );
}

function SessionCard({ id }: { id: string }) {
  const s = useStore().sessions.find((x) => x.id === id)!;
  const p = PROFILES[s.profile];
  const last = s.calls[s.calls.length - 1];
  return (
    <Link to={`/session/${id}`} className="block">
      <Card className="animate-ow-in transition hover:border-primary/50">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <StateBadge s={s.state} />
            <h3 className="mt-2 truncate font-semibold">{s.title}</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">{p.name} profile · {s.calls.length} tool calls · {s.id}</p>
          </div>
          <ChevronRight className="mt-1 size-5 shrink-0 text-muted-foreground" />
        </div>
        {last && <p className="mt-3 truncate font-mono text-xs text-muted-foreground">last: {last.tool} → {last.decision.toUpperCase()}</p>}
        <div className="mt-3"><Budget {...s.budget} /></div>
      </Card>
    </Link>
  );
}
