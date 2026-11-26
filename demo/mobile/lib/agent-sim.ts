import type { AgentRole, ApprovalRequest, EnvConfig, ToolCallEvent } from './mock-data'

export interface SimulatedStep {
  delayMs: number
  statusLabel: string
  toolCall?: Omit<ToolCallEvent, 'id' | 'seq' | 'timestamp'>
  previewTitle?: string
  previewLines?: string[]
}

export interface SimulatedResponse {
  title: string
  steps: SimulatedStep[]
  finalText: string
  approval?: Omit<ApprovalRequest, 'id' | 'createdAt' | 'expiresAt'>
}

export interface SimulationOptions {
  gmailBlocked?: boolean
  stoppedTools?: string[]
}

/**
 * Generates a realistic, prompt-aware MCP execution plan & response
 * with ~11-12s background execution pacing and tool permission checks.
 */
export function buildSimulation(
  prompt: string,
  role: AgentRole,
  env: EnvConfig,
  opts: SimulationOptions = {},
): SimulatedResponse {
  const lower = prompt.toLowerCase().trim()
  const targetEnv = env.mode === 'desktop' ? 'Remote Desktop' : `VM (${env.vmImage})`

  // Check if user requested Gmail/email while gmail is stopped in Permissions
  if ((lower.includes('gmail') || lower.includes('email') || lower.includes('mail') || lower.includes('draft')) && opts.gmailBlocked) {
    return {
      title: 'Gmail Draft (Blocked)',
      steps: [
        {
          delayMs: 1400,
          statusLabel: 'Checking MCP permissions...',
          toolCall: {
            tool: 'gmail.create_draft',
            args: { env: targetEnv, prompt: prompt.slice(0, 40) },
            decision: 'block',
            riskTier: 'sensitive',
            reversible: false,
            reason: 'POLICY_DENY — gmail access stopped by user',
          },
          previewTitle: 'MCP Gateway — Blocked',
          previewLines: [
            'Checking tool scope: gmail',
            '[BLOCKED] gmail server is stopped in Permissions',
            'Aborted execution cleanly',
          ],
        },
      ],
      finalText:
        `Gmail access is not available by MCP agent right now because **gmail** was stopped in your Tools & Permissions tab.\n\nRe-enable **gmail** in the **Tools** tab if you want me to compose or read drafts.`,
    }
  }

  // 0. Conversational follow-up ("yes", "sure", "ok", "do it", "go ahead")
  if (['yes', 'yeah', 'yep', 'sure', 'ok', 'okay', 'do it', 'go ahead', 'please do'].includes(lower)) {
    return {
      title: 'Follow-up Confirmed',
      steps: [
        {
          delayMs: 3600,
          statusLabel: 'Working on it...',
          toolCall: {
            tool: 'agent.execute_followup',
            args: { env: targetEnv, confirmed: true },
            decision: 'allow',
            riskTier: 'safe',
            reversible: true,
            durationMs: 140,
          },
          previewTitle: `${targetEnv} — Completed`,
          previewLines: [
            'Applying confirmed action...',
            'Updated reminder & synced state',
            '[OK] Done — sent confirmation to phone',
          ],
        },
        {
          delayMs: 3800,
          statusLabel: 'Verifying state...',
        },
        {
          delayMs: 3700,
          statusLabel: 'Finalizing response...',
        },
      ],
      finalText:
        `Done! I've completed that on **${targetEnv}** and synced the update back. Let me know what you'd like to tackle next.`,
    }
  }

  // 0b. Gmail draft prompt (e.g. "draft a gmail for me to Shreya")
  if (lower.includes('gmail') || lower.includes('email') || lower.includes('mail') || lower.includes('draft')) {
    return {
      title: 'Gmail Draft — Shreya',
      steps: [
        {
          delayMs: 3600,
          statusLabel: 'Working on it...',
          toolCall: {
            tool: 'gmail.list_threads',
            args: { query: 'Shreya project', limit: 3 },
            decision: 'allow',
            riskTier: 'safe',
            reversible: true,
            durationMs: 210,
          },
          previewTitle: `${targetEnv} — Gmail`,
          previewLines: [
            'Found thread: "Re: Today 4:30 PM sync"',
            'Loading context from project calendar...',
          ],
        },
        {
          delayMs: 4100,
          statusLabel: 'Working on it...',
          toolCall: {
            tool: 'gmail.create_draft',
            args: { to: 'shreya@team.dev', subject: 'Agenda for 4:30 PM project meeting' },
            decision: 'allow',
            riskTier: 'safe',
            reversible: true,
            durationMs: 310,
          },
          previewTitle: `${targetEnv} — Gmail Draft`,
          previewLines: [
            'To: shreya@team.dev',
            'Subject: Agenda for 4:30 PM project meeting',
            'Body: Hi Shreya, sharing the updated demo points...',
            '[OK] Draft saved (#dr_402)',
          ],
        },
        {
          delayMs: 3600,
          statusLabel: 'Checking send policy...',
          toolCall: {
            tool: 'gmail.send',
            args: { draftId: 'dr_402' },
            decision: 'ask',
            riskTier: 'sensitive',
            reversible: false,
            reason: 'Requires your approval before sending',
          },
        },
      ],
      approval: {
        tool: 'gmail.send',
        args: { to: 'shreya@team.dev', draftId: 'dr_402' },
        riskTier: 'sensitive',
        reversible: false,
        summary: 'Send drafted email to shreya@team.dev?',
      },
      finalText:
        `I drafted the Gmail to **shreya@team.dev** on ${targetEnv}:\n\n` +
        `*"Hi Shreya, quick update ahead of our 4:30 PM project meeting — the Python gateway module is running and tests pass. Want to review the slides right after?"*\n\n` +
        `The draft is saved (` + '`#dr_402`' + `). Tap **Approve** below if you want me to send it, or keep it as a draft.`,
    }
  }

  // 0c. Google Photos / App Store
  if (lower.includes('photo') || lower.includes('store')) {
    const isPhotos = lower.includes('photo')
    return {
      title: isPhotos ? 'Google Photos Organizer' : 'App Store Check',
      steps: [
        {
          delayMs: 3700,
          statusLabel: 'Working on it...',
          toolCall: {
            tool: isPhotos ? 'google_photos.list_recent' : 'app_store.search',
            args: { env: targetEnv, limit: 10 },
            decision: 'allow',
            riskTier: 'safe',
            reversible: true,
            durationMs: 210,
          },
          previewTitle: `${targetEnv} — ${isPhotos ? 'Google Photos' : 'App Store'}`,
          previewLines: isPhotos
            ? ['Scanning recent uploads...', 'Found 14 screenshots & 6 whiteboard photos', 'Suggested album: "Overwatch Hackathon"']
            : ['Querying App Store catalog...', 'Found 3 matching tools', 'Ready for install (requires approval)'],
        },
        {
          delayMs: 4100,
          statusLabel: 'Working on it...',
        },
        {
          delayMs: 3600,
          statusLabel: 'Preparing summary...',
        },
      ],
      finalText: isPhotos
        ? `I checked **Google Photos** on ${targetEnv} and found **14 screenshots** and **6 whiteboard photos** from this week.\n\nShould I group them into an **"Overwatch Hackathon"** album for you?`
        : `I searched the **App Store** on ${targetEnv} and found the top matching packages. Want me to install one?`,
    }
  }

  // 1. Calendar / Reminders / Deadlines (~11.6s total)
  if (lower.includes('calendar') || lower.includes('reminder') || lower.includes('deadline')) {
    return {
      title: 'Calendar Deadlines & Reminders',
      steps: [
        {
          delayMs: 3700,
          statusLabel: 'Working on it...',
          toolCall: {
            tool: 'calendar.open',
            args: { env: targetEnv, view: 'agenda' },
            decision: 'allow',
            riskTier: 'safe',
            reversible: true,
            durationMs: 180,
          },
          previewTitle: `${targetEnv} — Calendar`,
          previewLines: [
            'Opening Calendar app...',
            'Reading events: Nov 26 - Dec 02',
            'Found 4 meetings & 3 reminders',
          ],
        },
        {
          delayMs: 4200,
          statusLabel: 'Working on it...',
          toolCall: {
            tool: 'reminders.scan',
            args: { lists: ['College', 'Project', 'Personal'], includeOverdue: true },
            decision: 'allow',
            riskTier: 'safe',
            reversible: true,
            durationMs: 240,
          },
          previewTitle: `${targetEnv} — Reminders`,
          previewLines: [
            '[!] Today 16:30  Project meeting with Shreya (Meet)',
            '[!] Today 19:00  Send slides to Prof. Nair',
            '[ ] Nov 27 11:00 Dentist appt - Indiranagar',
            '[ ] Nov 28 18:00 Fix auth bug with Aarav',
          ],
        },
        {
          delayMs: 3600,
          statusLabel: 'Checking conflicts...',
          toolCall: {
            tool: 'calendar.check_conflicts',
            args: { windowHours: 72 },
            decision: 'allow',
            riskTier: 'safe',
            reversible: true,
            durationMs: 95,
          },
        },
      ],
      finalText:
        `Here is your schedule and upcoming deadlines (checked on ${targetEnv}):\n\n` +
        `1. **Today · 4:30 PM** — Project meeting with Shreya *(Google Meet)*\n` +
        `2. **Today · 7:00 PM** — Send Overwatch slides to Prof. Nair *(Urgent)*\n` +
        `3. **Tomorrow · 11:00 AM** — Dentist appointment at Indiranagar\n` +
        `4. **Sat, Nov 28 · 6:00 PM** — Debug auth module with Aarav\n\n` +
        `Want me to draft an email to Shreya with the agenda before 4:30 PM?`,
    }
  }

  // 2. Website / Application Form Filling (~11.8s total)
  if (lower.includes('website') || lower.includes('form') || lower.includes('fill') || lower.includes('apply') || lower.includes('http')) {
    const urlMatch = prompt.match(/https?:\/\/[^\s]+/i)
    const targetUrl = urlMatch ? urlMatch[0] : 'https://portal.example.org/apply'

    return {
      title: 'Application Form Assistant',
      steps: [
        {
          delayMs: 3800,
          statusLabel: 'Working on it...',
          toolCall: {
            tool: 'browser.navigate',
            args: { url: targetUrl, env: targetEnv },
            decision: 'allow',
            riskTier: 'safe',
            reversible: true,
            durationMs: 540,
          },
          previewTitle: `${targetEnv} — Browser`,
          previewLines: [
            `GET ${targetUrl}`,
            '200 OK · Loaded "2026 Application Form"',
            'Detected 6 input fields + 1 upload',
          ],
        },
        {
          delayMs: 4300,
          statusLabel: 'Working on it...',
          toolCall: {
            tool: 'browser.fill_fields',
            args: {
              fullName: 'Poorn',
              email: 'poorn@overwatch.dev',
              track: 'AI Systems & Security',
              fieldsFilled: 6,
            },
            decision: 'allow',
            riskTier: 'safe',
            reversible: true,
            durationMs: 390,
          },
          previewTitle: `${targetEnv} — Form Preview`,
          previewLines: [
            '[OK] Full Name: Poorn',
            '[OK] Email: poorn@overwatch.dev',
            '[OK] Project: Overwatch MCP Control Plane',
            '[OK] Repo: github.com/poorn/overwatch',
            '[PAUSE] Stopped before clicking [Submit]',
          ],
        },
        {
          delayMs: 3500,
          statusLabel: 'Checking submission gate...',
          toolCall: {
            tool: 'browser.submit_form',
            args: { url: targetUrl, action: 'click #submit-btn' },
            decision: 'ask',
            riskTier: 'sensitive',
            reversible: false,
            reason: 'External form submission requires your approval',
          },
        },
      ],
      approval: {
        tool: 'browser.submit_form',
        args: { url: targetUrl, fields: 6 },
        riskTier: 'sensitive',
        reversible: false,
        summary: `Submit filled application form on ${targetUrl}`,
      },
      finalText:
        `I opened **${targetUrl}** on ${targetEnv} and filled out all 6 fields using your profile.\n\n` +
        `The MCP gateway paused right before clicking **Submit** because submitting an external form is irreversible. Tap **Approve** below to submit, or **Deny** to inspect it yourself.`,
    }
  }

  // 3. Python Project / Resume Building (~11.5s total)
  if (lower.includes('python') || lower.includes('resume') || lower.includes('project') || lower.includes('build') || lower.includes('code')) {
    return {
      title: 'Python Project Workspace',
      steps: [
        {
          delayMs: 3600,
          statusLabel: 'Working on it...',
          toolCall: {
            tool: 'files.scan_workspace',
            args: { path: '/workspace/overwatch-agent', env: targetEnv },
            decision: 'allow',
            riskTier: 'safe',
            reversible: true,
            durationMs: 110,
          },
          previewTitle: `${targetEnv} — Terminal`,
          previewLines: [
            '$ git status -s',
            ' M src/agent/gateway.py',
            ' M tests/test_policy.py',
            'Last commit: "wip: policy evaluator"',
          ],
        },
        {
          delayMs: 4300,
          statusLabel: 'Working on it...',
          toolCall: {
            tool: 'terminal.run',
            args: { cmd: 'pytest tests/ -q' },
            decision: 'allow',
            riskTier: 'safe',
            reversible: false,
            durationMs: 920,
          },
          previewTitle: `${targetEnv} — pytest`,
          previewLines: [
            '$ pytest tests/ -q',
            '.............. [100%]',
            '14 passed in 0.84s',
            '[OK] Environment healthy (Python 3.12)',
          ],
        },
        {
          delayMs: 3600,
          statusLabel: 'Working on it...',
          toolCall: {
            tool: 'files.edit',
            args: { path: 'src/agent/gateway.py', change: 'Added async timeout & retry guard' },
            decision: 'allow',
            riskTier: 'safe',
            reversible: true,
            durationMs: 75,
          },
        },
      ],
      finalText:
        `Resumed your Python project on **${targetEnv}**:\n\n` +
        `• Picked up unfinished changes in \`src/agent/gateway.py\`\n` +
        `• Ran \`pytest tests/ -q\` — **14 passed** in 0.84s\n` +
        `• Added the async timeout & retry guard\n\n` +
        `Ready whenever you want to wire the CLI entrypoint or push to GitHub.`,
    }
  }

  // 4. Dynamic fallback for ANY custom prompt (~11.2s total)
  const snippet = prompt.trim().slice(0, 44) + (prompt.trim().length > 44 ? '...' : '')
  const toolByRole: Record<AgentRole, { tool: string; args: Record<string, unknown>; preview: string[] }> = {
    general: {
      tool: 'desktop.inspect',
      args: { query: snippet, env: targetEnv },
      preview: [`Target: ${targetEnv}`, `Task: "${snippet}"`, 'Checking active windows...', '[OK] Action completed'],
    },
    code: {
      tool: 'terminal.run',
      args: { cmd: `overwatch-exec --task "${snippet}"`, env: targetEnv },
      preview: [`$ overwatch-exec --task "${snippet}"`, 'Inspecting workspace files...', '[OK] Checks passed (exit 0)'],
    },
    research: {
      tool: 'browser.search',
      args: { q: snippet, sources: 4 },
      preview: [`Searching: "${snippet}"`, 'Fetched 4 primary sources', '[OK] Summary compiled'],
    },
  }
  const picked = toolByRole[role]

  return {
    title: snippet || 'New Task',
    steps: [
      {
        delayMs: 3800,
        statusLabel: 'Working on it...',
        toolCall: {
          tool: picked.tool,
          args: picked.args,
          decision: 'allow',
          riskTier: 'safe',
          reversible: true,
          durationMs: 260,
        },
        previewTitle: `${targetEnv} — Live`,
        previewLines: picked.preview,
      },
      {
        delayMs: 4100,
        statusLabel: 'Working on it...',
      },
      {
        delayMs: 3400,
        statusLabel: 'Verifying policy...',
        toolCall: {
          tool: 'policy.verify',
          args: { role, mode: env.mode },
          decision: 'allow',
          riskTier: 'safe',
          reversible: true,
          durationMs: 42,
        },
      },
    ],
    finalText:
      `Done with **"${prompt.trim()}"** under **${role === 'general' ? 'General Work' : role === 'code' ? 'Code' : 'Research'}** mode on **${targetEnv}**.\n\n` +
      `All MCP gateway checks passed. Tell me what you'd like to do next.`,
  }
}
