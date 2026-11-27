import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ShieldAlert, Undo2, X } from "lucide-react";
import type { ToolCall } from "../lib/mock";
import { REASONS } from "../lib/mock";
import { Btn, RiskBadge } from "./ui";

export function ApprovalSheet({ call, onDecide, onClose }: { call: ToolCall; onDecide: (approve: boolean, expired?: boolean) => void; onClose: () => void }) {
  const [left, setLeft] = useState(90);
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; }, [onClose]);
  useEffect(() => {
    const t = setInterval(() => setLeft((l) => l - 1), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => { if (left <= 0) onDecide(false, true); }, [left, onDecide]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.querySelector<HTMLElement>("[role=dialog] button[data-first-action]")?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") closeRef.current(); };
    window.addEventListener("keydown", key);
    return () => { document.body.style.overflow = oldOverflow; window.removeEventListener("keydown", key); previous?.focus(); };
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-background/70 backdrop-blur-sm sm:items-center" role="dialog" aria-modal="true" aria-labelledby="ap-title">
      <div className="animate-ow-sheet max-h-[92dvh] w-full overflow-y-auto rounded-t-2xl border bg-popover p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] sm:max-w-md sm:rounded-2xl">
        <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-border sm:hidden" />
        <div className="flex items-start gap-3">
          <div className="grid size-10 shrink-0 place-items-center rounded-lg bg-ask/15 text-ask"><ShieldAlert className="size-5" /></div>
          <div className="min-w-0 flex-1">
            <p className="text-xs text-muted-foreground">Approval required · {call.reason}</p>
            <h2 id="ap-title" className="truncate font-mono text-lg font-semibold">{call.tool}</h2>
          </div>
          <button onClick={onClose} aria-label="Close" className="grid size-11 place-items-center rounded-lg text-muted-foreground hover:bg-muted"><X className="size-5" /></button>
        </div>
        <p className="mt-3 text-sm text-muted-foreground">{REASONS[call.reason]}</p>

        <div className="mt-4 rounded-lg border bg-surface p-3">
          <p className="mb-2 text-[11px] uppercase tracking-wider text-muted-foreground">Sanitized arguments</p>
          <dl className="space-y-1 font-mono text-[13px]">
            {Object.entries(call.args).map(([k, v]) => (
              <div key={k} className="flex gap-2"><dt className="shrink-0 text-muted-foreground">{k}:</dt><dd className="min-w-0 break-all">{v}</dd></div>
            ))}
          </dl>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
          <div className="rounded-lg border p-3"><p className="text-[11px] text-muted-foreground">Risk tier</p><RiskBadge r={call.risk} /></div>
          <div className="rounded-lg border p-3"><p className="text-[11px] text-muted-foreground">Expires in</p><p className={`font-mono font-semibold ${left < 20 ? "text-block" : "text-ask"}`}>{Math.floor(Math.max(0, left) / 60)}:{String(Math.max(0, left) % 60).padStart(2, "0")}</p></div>
        </div>

        {call.reversible ? (
          <p className="mt-3 flex items-center gap-2 text-sm text-allow"><Undo2 className="size-4" /> Reversible — can be rolled back in the workspace.</p>
        ) : (
          <div className="mt-3 flex gap-2 rounded-lg border border-block/40 bg-block/10 p-3 text-sm">
            <AlertTriangle className="size-4 shrink-0 text-block" />
            <p><strong className="text-block">Cannot be undone.</strong> Once uploaded, the file leaves the sandbox and OverWatch cannot recall it.</p>
          </div>
        )}
        <p className="mt-3 text-xs text-muted-foreground">If the timer runs out, the request is denied automatically.</p>

        <div className="mt-5 grid grid-cols-2 gap-3">
          <Btn data-first-action="true" variant="danger" className="min-h-12" onClick={() => onDecide(false)}>Deny</Btn>
          <Btn variant="approve" className="min-h-12" onClick={() => onDecide(true)}>Approve</Btn>
        </div>
      </div>
    </div>
  );
}
