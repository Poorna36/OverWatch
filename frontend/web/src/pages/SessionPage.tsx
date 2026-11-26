import { Link, useParams } from "react-router-dom";
import { useState } from "react";
import { ArrowLeft, Hand, MonitorPlay, Pause, Play, Plus, Power, Image, Type, Wifi } from "lucide-react";
import { toast } from "../lib/toast";
import { PROFILES } from "../lib/mock";
import { useStore, type Tier } from "../lib/store";
import { ApprovalSheet } from "../components/ApprovalSheet";
import { Btn, Budget, CallRow, Card, StateBadge } from "../components/ui";
import { cn } from "../lib/utils";

const TIERS: { id: Tier; label: string; icon: typeof Image }[] = [
  { id: "video", label: "Video", icon: MonitorPlay },
  { id: "screenshot", label: "Screens", icon: Image },
  { id: "text", label: "Text", icon: Type },
];
const relativeSince = (iso: string) => {
  const minutes = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 60000));
  return minutes < 1 ? "just now" : minutes < 60 ? `${minutes}m ago` : `${Math.floor(minutes / 60)}h ago`;
};

export function SessionPage() {
  const { id } = useParams<{ id: string }>();
  const store = useStore();
  const s = store.sessions.find((x) => x.id === id);
  const [review, setReview] = useState<number | null>(null);
  if (!s) return <Card className="text-center"><p>Session not found.</p><Link to="/" className="text-primary underline">Back to sessions</Link></Card>;
  const pending = s.calls.find((c) => c.status === "pending");
  const reviewing = s.calls.find((c) => c.seq === review);
  const ended = s.state === "ENDED";
  const paused = s.state === "PAUSED";
  const feed = [...s.calls].reverse();

  const Feed = (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Tool calls · {s.calls.length}</h2>
        {!ended && <Btn variant="ghost" className="min-h-9 px-2 text-xs" disabled={paused} onClick={() => store.appendDemo(s.id)}><Plus className="size-3.5" /> Next step</Btn>}
      </div>
      {feed.length === 0 ? <Card className="text-center text-sm text-muted-foreground">Waiting for the agent's first tool call…</Card> : (
        <ul className="space-y-2">{feed.map((c) => <CallRow key={c.seq} c={c} onReview={() => setReview(c.seq)} />)}</ul>
      )}
    </section>
  );

  return (
    <div className="space-y-4">
      <Link to="/" className="inline-flex min-h-11 items-center gap-1 text-sm text-muted-foreground"><ArrowLeft className="size-4" /> Sessions</Link>
      <div>
        <StateBadge s={s.state} big />
        <h1 className="mt-2 text-xl font-bold tracking-tight">{s.title}</h1>
        <p className="font-mono text-xs text-muted-foreground">{PROFILES[s.profile].name} · started {relativeSince(s.startedAt)} · {s.id} · sandbox: hardened container</p>
        {s.title.startsWith("[Dry run]") && <p className="mt-1 font-mono text-xs text-ask">Dry run</p>}
      </div>

      {pending && !ended && (
        <button onClick={() => setReview(pending.seq)} className="flex w-full items-center gap-3 rounded-xl border border-ask/50 bg-ask/10 p-3 text-left">
          <span className="relative flex size-2.5"><span className="absolute inline-flex size-full animate-ping rounded-full bg-ask opacity-70" /><span className="relative inline-flex size-2.5 rounded-full bg-ask" /></span>
          <span className="min-w-0 flex-1 text-sm"><strong className="text-ask">Approval needed:</strong> <span className="font-mono">{pending.tool}</span></span>
          <span className="text-sm font-semibold text-ask">Review</span>
        </button>
      )}

      <div className="flex gap-1 rounded-lg border bg-surface p-1" role="radiogroup" aria-label="Bandwidth tier">
        {TIERS.map((t) => (
          <button key={t.id} role="radio" aria-checked={store.tier === t.id} onClick={() => store.setTier(t.id)} className={cn("flex min-h-10 flex-1 items-center justify-center gap-1.5 rounded-md text-xs font-medium text-muted-foreground", store.tier === t.id && "bg-accent text-foreground")}>
            <t.icon className="size-4" /> {t.label}
          </button>
        ))}
      </div>

      {store.tier === "text" ? Feed : (
        <>
          <div className="relative aspect-video overflow-hidden rounded-xl border bg-surface scanlines">
            <div className="absolute inset-3 rounded-md border bg-card/60 p-3 font-mono text-[10px] leading-relaxed text-muted-foreground sm:text-xs">
              <p className="text-foreground">agent@sandbox:/workspace/api-server$</p>
              {s.calls.slice(-4).map((c) => <p key={c.seq} className="truncate">&gt; {c.tool} {Object.values(c.args)[0]}</p>)}
              {!paused && !ended && <p className="animate-pulse">▍</p>}
            </div>
            <div className="absolute left-2 top-2 flex items-center gap-1.5 rounded bg-background/80 px-2 py-0.5 font-mono text-[10px]">
              {store.tier === "video" ? <><span className="size-1.5 rounded-full bg-block" /> LIVE · 12fps</> : <><Image className="size-3" /> every 5s</>}
            </div>
            {(paused || ended) && <div className="absolute inset-0 grid place-items-center bg-background/60 font-mono text-sm font-semibold">{s.state}</div>}
          </div>
          <Card className="py-3"><Budget {...s.budget} /></Card>
        </>
      )}

      <div className="sticky bottom-[calc(4.5rem+env(safe-area-inset-bottom))] z-20 grid grid-cols-3 gap-2 rounded-xl border bg-popover/95 p-2 backdrop-blur lg:bottom-4">
        <Btn variant="secondary" disabled={ended} onClick={() => store.setState(s.id, paused ? "ACTIVE" : "PAUSED")}>{paused ? <><Play className="size-4" /> Resume</> : <><Pause className="size-4" /> Pause</>}</Btn>
        <Btn variant="secondary" disabled={ended} onClick={() => toast("Take over requested", { description: "Handing desktop control to your device." })}><Hand className="size-4" /> Take over</Btn>
        <Btn variant="danger" disabled={ended} onClick={() => { store.setState(s.id, "ENDED"); toast("Session ended"); }}><Power className="size-4" /> End</Btn>
      </div>

      {store.tier === "text" ? <Card className="py-3"><Budget {...s.budget} /></Card> : Feed}
      <p className="flex items-center justify-center gap-1 text-xs text-muted-foreground"><Wifi className="size-3" /> Stream tier: {store.tier}</p>

      {reviewing && reviewing.status === "pending" && (
        <ApprovalSheet
          call={reviewing}
          onClose={() => setReview(null)}
          onDecide={(ok, expired) => {
            store.decide(s.id, reviewing.seq, ok, expired);
            setReview(null);
            toast(ok ? "Approved — files.upload executed" : expired ? "Approval expired — denied" : "Denied — call not executed");
          }}
        />
      )}
    </div>
  );
}
