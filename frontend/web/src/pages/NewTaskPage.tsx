import { useNavigate } from "react-router-dom";
import { useState } from "react";
import { Code2, Globe, Sun, Info, ArrowRight } from "lucide-react";
import { PROFILES, type ProfileId } from "../lib/mock";
import { useStore } from "../lib/store";
import { Btn, Card } from "../components/ui";
import { cn } from "../lib/utils";

const icons = { coding: Code2, general: Globe, daily: Sun };

export function NewTaskPage() {
  const nav = useNavigate();
  const { draft, setDraft } = useStore();
  const [prompt, setPrompt] = useState(draft?.prompt ?? "Fix flaky auth tests in api-server");
  const [profile, setProfile] = useState<ProfileId>(draft?.profile ?? "coding");
  const [dry, setDry] = useState(draft?.dryRun ?? false);
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold tracking-tight">New task</h1>
      <label className="block">
        <span className="text-sm font-medium">What should the agent do?</span>
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={4} className="mt-2 w-full rounded-xl border bg-surface p-3 text-base outline-none focus:ring-2 focus:ring-ring" placeholder="e.g. Fix flaky auth tests in api-server" />
      </label>
      <fieldset>
        <legend className="text-sm font-medium">Profile</legend>
        <div className="mt-2 grid gap-2 sm:grid-cols-3">
          {(Object.keys(PROFILES) as ProfileId[]).map((id) => {
            const p = PROFILES[id]; const I = icons[id];
            return (
              <button key={id} type="button" aria-pressed={profile === id} onClick={() => setProfile(id)} className={cn("rounded-xl border bg-card p-4 text-left transition", profile === id && "border-primary ring-1 ring-primary")}>
                <div className="flex items-center justify-between"><I className="size-5 text-primary" /></div>
                <p className="mt-2 font-semibold">{p.name}</p>
                <p className="text-xs text-muted-foreground">{p.desc}</p>
                <p className="mt-2 font-mono text-[11px] text-muted-foreground">{p.tools.join(" · ")}</p>
              </button>
            );
          })}
        </div>
        <p className="mt-2 flex gap-2 text-xs text-muted-foreground"><Info className="size-4 shrink-0" /> The profile limits which MCP tools exist in the session. Anything outside it is blocked by the gateway, regardless of what the agent asks for.</p>
      </fieldset>
      <Card className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium">Dry run</p>
          <p className="text-xs text-muted-foreground">Preview planned tool calls without executing writes.</p>
        </div>
        <button role="switch" aria-checked={dry} aria-label="Dry run" onClick={() => setDry(!dry)} className={cn("relative h-7 w-12 shrink-0 rounded-full transition", dry ? "bg-primary" : "bg-muted")}>
          <span className={cn("absolute top-1 size-5 rounded-full bg-foreground transition-all", dry ? "left-6" : "left-1")} />
        </button>
      </Card>
      <Btn className="min-h-12 w-full" disabled={!prompt.trim()} onClick={() => { setDraft({ prompt: prompt.trim(), profile, dryRun: dry }); nav("/plan"); }}>
        Start task <ArrowRight className="size-4" />
      </Btn>
    </div>
  );
}
