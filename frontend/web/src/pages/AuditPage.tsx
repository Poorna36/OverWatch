import { useMemo, useState } from "react";
import { Download, Link2, CheckCircle2 } from "lucide-react";
import { mockHash, REASONS, type Decision } from "../lib/mock";
import { useStore } from "../lib/store";
import { Btn, Card, DecisionBadge, fmtTime } from "../components/ui";
import { cn } from "../lib/utils";

export function AuditPage() {
  const { sessions } = useStore();
  const [f, setF] = useState<"all" | Decision>("all");
  const records = useMemo(() => {
    const all = sessions.flatMap((s) => s.calls.map((c) => ({ ...c, session: s.id }))).sort((a, b) => a.ts.localeCompare(b.ts));
    return all.reduce<Array<typeof all[number] & { prev: string; hash: string }>>((chain, r) => {
      const prev = chain.length ? chain[chain.length - 1]!.hash : "00000000";
      const hash = mockHash(prev + JSON.stringify(r));
      chain.push({ ...r, prev, hash });
      return chain;
    }, []);
  }, [sessions]);
  const shown = records.filter((r) => f === "all" || r.decision === f).reverse();

  const exportJson = () => {
    const blob = new Blob([JSON.stringify({ kind: "overwatch.audit.receipt", exportedAt: new Date().toISOString(), records }, null, 2)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "overwatch-audit-receipt.json"; a.click(); URL.revokeObjectURL(a.href);
  };

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between gap-3">
        <h1 className="text-2xl font-bold tracking-tight">Audit</h1>
        <Btn variant="secondary" onClick={exportJson}><Download className="size-4" /> Export receipt</Btn>
      </div>
      <Card className="flex gap-3 border-allow/40">
        <CheckCircle2 className="size-5 shrink-0 text-allow" />
        <div className="text-sm">
          <p className="font-semibold">Hash chain verified</p>
          <p className="text-muted-foreground">{records.length} records chained in order.</p>
        </div>
      </Card>
      <div className="flex gap-2 overflow-x-auto" role="tablist">
        {(["all", "allow", "ask", "block"] as const).map((k) => (
          <button key={k} role="tab" aria-selected={f === k} onClick={() => setF(k)} className={cn("min-h-10 shrink-0 rounded-full border px-4 font-mono text-xs uppercase", f === k ? "border-primary bg-accent text-foreground" : "text-muted-foreground")}>
            {k} {k !== "all" && <span className="opacity-60">{records.filter((r) => r.decision === k).length}</span>}
          </button>
        ))}
      </div>
      <ol className="relative space-y-3 border-l pl-5">
        {shown.map((r) => (
          <li key={r.session + r.seq} className="animate-ow-in relative">
            <span className="absolute -left-[26px] top-3 size-2.5 rounded-full border-2 border-background bg-primary" />
            <Card className="p-3">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate font-mono text-sm font-semibold">{r.tool}</span>
                <DecisionBadge d={r.decision} />
              </div>
              <p className="mt-1 text-xs"><span className="font-mono">{r.reason}</span> <span className="text-muted-foreground">— {REASONS[r.reason]}</span></p>
              <p className="mt-2 flex flex-wrap items-center gap-x-3 font-mono text-[11px] text-muted-foreground">
                <span>{fmtTime(r.ts)}</span><span>{r.session}#{r.seq}</span>
                <span className="flex items-center gap-1"><Link2 className="size-3" />{r.prev.slice(0, 6)}→{r.hash.slice(0, 6)}</span>
              </p>
            </Card>
          </li>
        ))}
        {shown.length === 0 && <p className="text-sm text-muted-foreground">No records for this filter.</p>}
      </ol>
    </div>
  );
}
