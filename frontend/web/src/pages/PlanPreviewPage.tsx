import { Navigate, useNavigate } from "react-router-dom";
import { AlertTriangle, Check, FlaskConical, Pencil, Undo2 } from "lucide-react";
import { toast } from "../lib/toast";
import { mockHash, PROFILE_PLAN_STEPS, PROFILES } from "../lib/mock";
import { useStore } from "../lib/store";
import { Btn, Card, RiskBadge } from "../components/ui";

export function PlanPreviewPage() {
  const nav = useNavigate();
  const { createSession, draft } = useStore();
  if (!draft) return <Navigate to="/new" replace />;
  const p = PROFILES[draft.profile];
  const steps = PROFILE_PLAN_STEPS[draft.profile];
  const start = () => { const id = createSession(draft.prompt, draft.profile, steps, draft.dryRun); nav(`/session/${id}`); };
  const hash = mockHash(JSON.stringify({ prompt: draft.prompt, profile: draft.profile, steps }));
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Plan preview</h1>
        <p className="mt-1 text-sm text-muted-foreground">{draft.prompt}</p>
        <p className="font-mono text-xs text-muted-foreground">{p.name} profile{draft.dryRun ? " · dry run" : ""}</p>
        <p className="font-mono text-xs text-muted-foreground">Plan hash: {hash}</p>
      </div>
      <ol className="space-y-2">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-3 rounded-xl border bg-card p-3">
            <span className="grid size-7 shrink-0 place-items-center rounded-full bg-muted font-mono text-xs">{i + 1}</span>
            <div className="min-w-0 flex-1">
              <p className="font-mono text-sm font-semibold">{s.tool}</p>
              <p className="text-sm text-muted-foreground">{s.desc}</p>
              <div className="mt-1 flex flex-wrap gap-3">
                <RiskBadge r={s.risk} />
                {s.reversible ? <span className="flex items-center gap-1 text-[11px] text-muted-foreground"><Undo2 className="size-3" />reversible</span> : <span className="flex items-center gap-1 text-[11px] font-semibold text-block"><AlertTriangle className="size-3" />irreversible · will ask</span>}
              </div>
            </div>
          </li>
        ))}
      </ol>
      <Card className="flex justify-between text-sm"><span className="text-muted-foreground">Estimated budget</span><span className="font-mono font-semibold">~$0.60 of $2.00</span></Card>
      <div className="grid grid-cols-3 gap-2">
        <Btn variant="secondary" onClick={() => nav("/new")}><Pencil className="size-4" /> Edit</Btn>
        <Btn variant="secondary" onClick={() => toast("Dry run complete", { description: `${steps.length} steps evaluated, 0 executed.` })}><FlaskConical className="size-4" /> Dry run</Btn>
        <Btn onClick={start}><Check className="size-4" /> Approve</Btn>
      </div>
      <p className="text-center text-xs text-muted-foreground">Off-plan calls escalate to approval automatically.</p>
    </div>
  );
}
