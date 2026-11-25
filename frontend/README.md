# Overwatch — Frontend

Demo frontend for the Overwatch MCP Control Plane. No backend required — all data is mocked.

## Structure

```
frontend/
├── web/        React + Vite + Tailwind dashboard (runs in any browser)
└── mobile/     Expo React Native app (iOS, Android, or web via Expo Go)
```

---

## Web dashboard

### Run locally

```bash
cd frontend/web
npm install       # already done if you followed setup
npm run dev       # → http://localhost:5173
```

### Pages
| Path | Screen |
|---|---|
| `/` | Home — session list, stats |
| `/new` | New Task — task input, profile picker, dry-run toggle |
| `/session/:id` | Session — live view, tool-call feed, approval modal, controls |
| `/audit` | Audit log — hash-chain, filter, export receipt |
| `/settings` | Settings — server URL, token, Google connector (demo) |

### Demo flow
1. Open `/` and click **"sess_abc123"** (the ACTIVE session), or go to `/session/sess_abc123`
2. Click **▶ Run demo** — the agent fires scripted events: allow → allow → allow → **block** (PATH_JAIL) → **ask** (approval modal pops) → **block** (EGRESS_DENIED)
3. The approval modal has a countdown timer; tap Approve or Deny
4. Click **End** → "Workspace destroyed" card → redirects home
5. Go to `/audit` to see the hash-chained log and export a JSON receipt

---

## Mobile app (Expo)

### Run on device / emulator

```bash
cd frontend/mobile
npm install       # already done
npx expo start    # scan QR with Expo Go, or press w for browser
```

### Screens
| Screen | Description |
|---|---|
| Home | Session list, stats, New task button |
| New Task | Text input, profile selector, dry-run toggle |
| Session | Live view mock, tool-call feed, budget bar, Pause/End controls |
| Approval sheet | Bottom sheet modal with countdown, Approve/Deny |
| Audit | Hash-chain status, filterable log, export |

### Demo flow (same story, mobile UI)
1. Home → tap **New task** → type a task → **Start session**
2. Session screen → tap **▶ Demo** → watch events stream in
3. Approval bottom sheet slides up → tap **Approve**
4. Tap **End** → "Workspace destroyed" → back to Home
5. Tap **Audit** tab from Home → see the log

---

## Design

- **Palette**: zinc-950 background, indigo accent, emerald/amber/rose for allow/ask/block
- **Font**: Inter (web) / system monospace for feed
- **Icons**: inline SVG (Lucide-style), no emoji icons
- **Motion**: CSS keyframe animations on new feed entries, approval countdown ring
- **No AI-generated imagery** — everything is code-drawn

## Tech

| App | Stack |
|---|---|
| Web | Vite · React 18 · TypeScript · Tailwind v4 · React Router 6 · Framer Motion |
| Mobile | Expo SDK 58 · React Native · React Navigation Stack · TypeScript |
