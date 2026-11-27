import { Lock } from "lucide-react";
import { useStore, type Tier } from "../lib/store";
import { Btn, Card } from "../components/ui";
import { cn } from "../lib/utils";

const input = "mt-1.5 w-full min-h-11 rounded-lg border bg-surface px-3 font-mono text-sm outline-none focus:ring-2 focus:ring-ring";

export function SettingsPage() {
  const { tier, setTier, settings, updateSettings, reset } = useStore();
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
      <Card className="space-y-4">
        <h2 className="font-semibold">Control plane</h2>
        <label className="block text-sm">Server URL<input value={settings.serverUrl} onChange={(e) => updateSettings({ serverUrl: e.target.value })} className={input} placeholder="https://overwatch.example.com" autoComplete="off" /></label>
        <label className="block text-sm">Session token<input type="password" value={settings.token} onChange={(e) => updateSettings({ token: e.target.value })} className={input} placeholder="Enter token" autoComplete="off" /></label>
        <p className="flex gap-2 text-xs text-muted-foreground"><Lock className="size-4 shrink-0" /> Saved locally in this browser.</p>
      </Card>
      <Card>
        <h2 className="font-semibold">Bandwidth preference</h2>
        <p className="text-xs text-muted-foreground">Default live-view tier for sessions.</p>
        <div className="mt-3 grid grid-cols-3 gap-2">
          {(["video", "screenshot", "text"] as Tier[]).map((t) => (
            <button key={t} aria-pressed={tier === t} onClick={() => setTier(t)} className={cn("min-h-11 rounded-lg border text-sm capitalize", tier === t ? "border-primary bg-accent" : "text-muted-foreground")}>{t}</button>
          ))}
        </div>
      </Card>
      <Card className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold">Google connector</p>
          <p className="text-xs text-muted-foreground">{settings.connector ? "Connected · gmail.list, calendar.read" : "Used by the Daily routine profile."}</p>
        </div>
        <Btn variant={settings.connector ? "danger" : "secondary"} className="shrink-0" onClick={() => updateSettings({ connector: !settings.connector })}>{settings.connector ? "Disconnect" : "Connect"}</Btn>
      </Card>
      <Card className="flex items-center justify-between gap-3"><div><h2 className="font-semibold">Reset workspace</h2><p className="text-xs text-muted-foreground">Restore default sessions and clear saved preferences.</p></div><Btn variant="danger" onClick={() => { if (window.confirm("Reset OverWatch sessions and settings?")) { reset(); } }}>Reset</Btn></Card>
    </div>
  );
}
