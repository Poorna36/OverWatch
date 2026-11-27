import { useEffect, useState } from "react";

type Notice = { message: string; description?: string; action?: { label: string; run: () => void } };

export function toast(message: string, options?: { description?: string; action?: { label: string; run: () => void } }) {
  window.dispatchEvent(new CustomEvent<Notice>("overwatch:toast", {
    detail: { message, ...(options?.description ? { description: options.description } : {}), ...(options?.action ? { action: options.action } : {}) },
  }));
}

export function ToastHost() {
  const [notice, setNotice] = useState<Notice | null>(null);
  useEffect(() => {
    const onToast = (event: Event) => setNotice((event as CustomEvent<Notice>).detail);
    window.addEventListener("overwatch:toast", onToast);
    return () => window.removeEventListener("overwatch:toast", onToast);
  }, []);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 3200);
    return () => window.clearTimeout(timer);
  }, [notice]);
  if (!notice) return null;
  return (
    <div role="status" className="fixed left-1/2 top-16 z-50 w-[min(24rem,calc(100vw-2rem))] -translate-x-1/2 rounded-xl border bg-popover p-4 shadow-2xl animate-ow-in">
      <p className="text-sm font-semibold">{notice.message}</p>
      {notice.description && <p className="mt-1 text-xs text-muted-foreground">{notice.description}</p>}
      {notice.action && <button className="mt-2 min-h-10 rounded px-3 font-semibold text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring" onClick={() => { notice.action?.run(); setNotice(null); }}>{notice.action.label}</button>}
    </div>
  );
}
