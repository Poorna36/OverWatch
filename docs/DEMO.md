# DEMO

Technical demo, about 3 minutes. One story, not a feature tour. Say "MCP", "gateway" and "firewall" out loud so judges link the visuals to the track.

## 1. Story

"I have a phone and no computer. I ask for a task. A sandboxed workspace spins up, an MCP agent does the work, and every tool call passes through one firewall. I watch, approve and stay in control."

## 2. MVP demo path (must work)

| Step | Action | What judges see | Features |
|---|---|---|---|
| 1 | Type the task on the phone: "Create a Python script that fetches a page title and run it" | New Task screen | F-05 |
| 2 | Session starts (pre-warmed) | State goes STARTING → ACTIVE; profile shown (`coding`) | F-01, F-09 |
| 3 | Agent works | Live desktop view plus tool-call feed: `files.write` ✓, `terminal.run` ✓ | F-03, F-04 |
| 4 | Agent tries a blocked action (scripted): write to `/etc/passwd` | Feed shows ✕ `PATH_JAIL`; audit entry appears | F-02, F-07 |
| 5 | Agent tries `files.upload` | Approval card on phone (push notification); tap Approve | F-06 |
| 6 | Live bypass attempt from the terminal: `curl https://evil.example` | Asked, then blocked by egress allowlist | F-02 |
| 7 | Task finishes | Result shown; tap End | F-05 |
| 8 | Teardown | "Workspace destroyed"; receipt with audit chain verified | F-08, F-07 |

## 3. STRETCH beats (add in this order if built)

- **Plan preview** before step 2: card shows tools, tiers, budget; tap Approve. (F-11)
- **Dry-run** then Apply: diff list matches what happens. (F-12)
- **Undo** one `files.write` from the timeline. (F-13)
- **Failure**: simulate an upstream error; problem report with a tappable fix. (F-14)
- **Weak network**: throttle; tier drops video → screenshots → text feed while the task continues; kbps meter visible. (F-15)
- **Voice**: speak a regional-language task; English text appears for confirmation. (F-16)

## 4. DEMO-only add-on (Gmail / Calendar)

Only if the MVP path is rock solid: `daily_routine` profile (test Google account). Agent reads the calendar, drafts a reply, **cannot send**; tap Send on the draft card; End session shows tokens revoked. (F-18)

## 5. Script (speaker notes)

1. "MCP gives AI hands. We give those hands a safe place to work, and a remote control in your pocket."
2. (Start task) "No laptop. This phone is the remote; the computer is a sandboxed container."
3. (Feed) "Everything the agent does is an MCP tool call, and every call goes through this gateway."
4. (Block) "The agent tried to write outside its workspace. Blocked, logged."
5. (Approval) "Anything sensitive needs a human tap."
6. (Curl) "Even if it runs code, the container can only reach an allowlist."
7. (End) "Session over: container destroyed, credentials gone, audit receipt verifiable."
8. Close: "The firewall and the sandbox are what make giving an agent real tools reasonable."

## 6. Pre-demo checklist

- [ ] Stack running locally and on the demo network; `.env` has LLM key
- [ ] Pre-warmed container image pulled; base image built
- [ ] Phone paired, push permission granted, server URL set
- [ ] Scripted triggers ready for steps 4 and 6 (a prepared task prompt that reliably does them)
- [ ] Bypass suite green on this machine
- [ ] Network throttling tool ready (if showing F-15)
- [ ] Recorded backup video available offline
- [ ] Second phone or emulator as a fallback

## 7. Fallbacks

| Failure | Fallback |
|---|---|
| Live view not working | Screenshot polling mode; narrate from the tool-call feed |
| LLM API slow or down | Replay a scripted event stream (`control-plane/dev/mock_stream.py`) against the real gateway |
| Phone can't connect | Emulator on the laptop; or play the backup video |
| Container slow to start | Use the pre-warmed container; skip the spin-up animation |
| Push notifications fail | Approval card still appears in-app |

## 8. Judge Q&A

- **Isn't this just remote desktop?** Remote desktop shows a screen. We add an agent, MCP tool access, a policy gateway, approvals, audit and a hardened sandbox.
- **What stops the agent doing harm?** Two layers: the gateway policy (allow/ask/block) and the container (read-only root, non-root, no capabilities, egress allowlist).
- **What if the policy misses something?** The container limits still apply; irreversible actions need a human tap; everything is audited.
- **Prompt injection?** Out of scope for the MVP; content is treated as data and sensitive actions require approval. We don't claim to detect injection.
- **Why MCP?** It lets us plug into existing servers and put one gateway in front of all of them.
- **Why a VM or container instead of the user's PC?** Many people don't have a computer, and a disposable workspace limits blast radius. Own-PC mode uses the same gateway (next step).
- **What about weak internet?** Tasks run server-side; live view degrades to a text feed and replays missed events on reconnect.
- **Cost per session?** Per-session TTL, resource limits and a budget meter.
