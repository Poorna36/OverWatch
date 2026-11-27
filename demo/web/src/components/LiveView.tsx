import { useState, useEffect } from 'react'
import { cn } from '../lib/utils'

interface LiveViewProps {
  sessionId: string
  active: boolean
  tier: 'video' | 'screenshots' | 'text'
  className?: string
}

// Simulated "desktop" frames that cycle to give the feel of a live view
const DESKTOP_FRAMES = [
  {
    label: 'Terminal — pip install requests',
    content: `$ pip install requests
Collecting requests
  Downloading requests-2.31.0-py3-none-any.whl (62 kB)
     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 62.0/62.0 kB 1.2 MB/s
Collecting charset-normalizer<4,>=2
Collecting idna<4,>=2.5
Collecting urllib3<3,>=1.21.1
Collecting certifi>=2017.4.17
Installing collected packages: certifi, idna, charset-normalizer, urllib3, requests
Successfully installed requests-2.31.0`,
  },
  {
    label: 'Editor — fetch_title.py',
    content: `import requests
from bs4 import BeautifulSoup
import sys

def fetch_title(url: str) -> str:
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'html.parser')
    return soup.title.string.strip() if soup.title else 'No title'

if __name__ == '__main__':
    url = sys.argv[1] if len(sys.argv) > 1 else 'https://example.com'
    print(f"Title: {fetch_title(url)}")`,
  },
  {
    label: 'Terminal — running script',
    content: `$ python fetch_title.py https://example.com
Title: Example Domain

$ python fetch_title.py https://python.org
Title: Welcome to Python.org`,
  },
]

export function LiveView({ sessionId: _id, active, tier, className }: LiveViewProps) {
  const [frameIdx, setFrameIdx] = useState(0)
  const [scanLine, setScanLine] = useState(0)

  useEffect(() => {
    if (!active || tier === 'text') return
    const id = setInterval(() => {
      setFrameIdx(i => (i + 1) % DESKTOP_FRAMES.length)
    }, 4000)
    return () => clearInterval(id)
  }, [active, tier])

  useEffect(() => {
    if (!active) return
    const id = setInterval(() => {
      setScanLine(s => (s + 1) % 100)
    }, 50)
    return () => clearInterval(id)
  }, [active])

  if (tier === 'text') {
    return (
      <div className={cn(
        'rounded-xl border border-[#45475a] bg-[#11111b] flex flex-col items-center justify-center gap-2 p-6',
        className,
      )}>
        <div className="text-[#6c7086] text-xs font-mono">≡ text tier — no live view</div>
        <div className="text-[#45475a] text-xs">Tool-call feed is the primary view</div>
      </div>
    )
  }

  if (!active) {
    return (
      <div className={cn(
        'rounded-xl border border-[#45475a] bg-[#11111b] flex items-center justify-center',
        className,
      )}>
        <div className="text-[#6c7086] text-sm">Session not active</div>
      </div>
    )
  }

  const frame = DESKTOP_FRAMES[frameIdx]

  return (
    <div className={cn(
      'rounded-xl border border-[#45475a] bg-[#11111b] overflow-hidden relative group',
      className,
    )}>
      {/* Fake title bar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[#313244] bg-[#181825]">
        <div className="flex gap-1.5">
          <div className="w-3 h-3 rounded-full bg-[#f38ba8]/70" />
          <div className="w-3 h-3 rounded-full bg-[#f9e2af]/70" />
          <div className="w-3 h-3 rounded-full bg-[#a6e3a1]/70" />
        </div>
        <span className="text-xs text-[#6c7086] font-mono flex-1 text-center">{frame.label}</span>
        <div className="flex items-center gap-1 text-[10px] text-[#a6e3a1] font-mono">
          <span className="animate-pulse-dot">●</span>
          <span>LIVE</span>
        </div>
      </div>

      {/* Terminal content */}
      <div className="p-4 font-mono text-xs leading-relaxed">
        <pre className={cn(
          'text-[#a6e3a1] whitespace-pre-wrap break-all transition-opacity duration-500',
          'opacity-90',
        )}>
          {frame.content}
        </pre>
        {/* Fake cursor blink */}
        <span className="inline-block w-2 h-3.5 bg-[#cba6f7] animate-pulse ml-0.5 align-middle" />
      </div>

      {/* Scan line overlay — gives CRT / broadcast feel */}
      <div
        className="absolute inset-x-0 h-6 pointer-events-none opacity-5 bg-gradient-to-b from-white/20 to-transparent transition-none"
        style={{ top: `${scanLine}%` }}
      />

      {/* Corner tier badge */}
      <div className="absolute bottom-2 right-2 text-[10px] font-mono text-[#6c7086] bg-[#11111b]/80 px-1.5 py-0.5 rounded">
        {tier}
      </div>
    </div>
  )
}
