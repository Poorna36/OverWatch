import { Link, useLocation } from "react-router-dom";
import { Home, Plus, ScrollText, Settings } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../lib/utils";

const NAV = [
  { to: "/", label: "Sessions", icon: Home },
  { to: "/new", label: "New task", icon: Plus },
  { to: "/audit", label: "Audit", icon: ScrollText },
  { to: "/settings", label: "Settings", icon: Settings },
] as const;

export function Shell({ children }: { children: ReactNode }) {
  const path = useLocation().pathname;
  const active = (to: string) => (to === "/" ? path === "/" || path.startsWith("/session") : path.startsWith(to));
  return (
    <div className="min-h-dvh lg:flex">
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col border-r bg-surface p-4 lg:flex">
        <Brand />
        <nav className="mt-8 space-y-1">
          {NAV.map((n) => (
            <Link key={n.to} to={n.to} className={cn("flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground", active(n.to) && "bg-accent text-foreground")}>
              <n.icon className="size-4" /> {n.label}
            </Link>
          ))}
        </nav>
      </aside>
      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b bg-background/90 px-4 backdrop-blur lg:hidden">
          <Brand />
        </header>
        <main className="mx-auto max-w-3xl px-4 pb-28 pt-4 lg:px-8 lg:pb-10 lg:pt-8">{children}</main>
        <nav className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-4 border-t bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
          {NAV.map((n) => (
            <Link key={n.to} to={n.to} className={cn("flex min-h-16 flex-col items-center justify-center gap-1 text-[11px] font-medium text-muted-foreground", active(n.to) && "text-primary")}>
              {n.to === "/new" ? (
                <span className="grid size-9 place-items-center rounded-full bg-primary text-primary-foreground"><n.icon className="size-5" /></span>
              ) : (
                <n.icon className="size-5" />
              )}
              {n.label}
            </Link>
          ))}
        </nav>
      </div>
    </div>
  );
}

function Brand() {
  return (
    <Link to="/" className="flex items-center gap-2">
      <span className="leading-tight">
        <span className="block text-sm font-bold tracking-tight">OverWatch</span>
        <span className="block font-mono text-[10px] text-muted-foreground">MCP Pocket Computer</span>
      </span>
    </Link>
  );
}
