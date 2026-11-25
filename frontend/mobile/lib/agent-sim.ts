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

/**
 * Generates a realistic, prompt-aware MCP execution plan & response
 * with 3-5 second background pacing per step.
 */
export function buildSimulation(prompt: string, role: AgentRole, env: EnvConfig): SimulatedResponse {
  const lower = prompt.toLowerCase().trim()
  const targetEnv = env.mode === 'desktop' ? 'Remote Desktop' : `VM (${env.vmImage})`

  // 0. Conversational follow-up ("yes", "sure", "ok", "do it", "go ahead", "no")
  if (['yes', 'yeah', 'yep', 'sure', 'ok', 'okay', 'do it', 'go ahead', 'please do'].includes(lower)) {
    return {
      title: 'Follow-up Confirmed',
      steps: [
        {
          delayMs: 3200,
          statusLabel: `Executing task on ${targetEnv}...`,
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
      ],
      finalText:
        `Done! I've completed that action on **${targetEnv}** and synced the update back to your phone. Let me know what you'd like to do next.`,
    }
  }

  // 0b. Google Photos / App Store / Gmail
  if (lower.includes('photo') || lower.includes('store') || lower.includes('gmail') || lower.includes('email')) {
    const isPhotos = lower.includes('photo')
    const isStore = lower.includes('store')
    return {
      title: isPhotos ? 'Google Photos Organizer' : isStore ? 'App Store Check' : 'Gmail Assistant',
      steps: [
        {
          delayMs: 3400,
          statusLabel: `Executing task — opening ${isPhotos ? 'Google Photos' : isStore ? 'App Store' : 'Gmail'} on ${targetEnv}...`,
          toolCall: {
            tool: isPhotos ? 'google_photos.list_recent' : isStore ? 'app_store.search' : 'gmail.list_unread',
            args: { env: targetEnv, limit: 10 },
            decision: 'allow',
            riskTier: 'safe',
            reversible: true,
            durationMs: 210,
          },
          previewTitle: `${targetEnv} — ${isPhotos ? 'Google Photos' : isStore ? 'App Store' : 'Gmail'}`,
          previewLines: isPhotos
            ? ['Scanning recent uploads...', 'Found 14 screenshots & 6 whiteboard photos', 'Suggested album: "Overwatch Hackathon"']
            : isStore
              ? ['Querying App Store catalog...', 'Found 3 matching tools', 'Ready for install (requires approval)']
              : ['Reading unread priority threads...', '2 messages need reply (Hackathon, Mentor)', 'Draft ready in Gmail'],
        },
      ],
      finalText: isPhotos
        ? `I checked **Google Photos** on ${targetEnv} and found **14 screenshots** and **6 whiteboard photos** from this week.\n\nShould I group them into an **"Overwatch Hackathon"** album for you?`
        : isStore
          ? `I searched the **App Store** on ${targetEnv} and found the top matching packages. Want me to install one? (I'll ask for your approval before installing.)`
          : `I scanned **Gmail** on ${targetEnv} — you have **2 priority emails** waiting. I drafted replies for both; should I open them for your review?`,
    }
  }

  // 1. Calendar / Reminders / Deadlines
  if (lower.includes('calendar') || lower.includes('reminder') || lower.includes('deadline')) {
    return {
      title: 'Calendar Deadlines & Reminders',
      steps: [
        {
          delayMs: 3200,
          statusLabel: `Executing task — opening Calendar on ${targetEnv}...`,
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
            'Reading events: Nov 26 - Dec 03',
            'Found 6 scheduled events, 4 reminders',
          ],
        },
        {
          delayMs: 3800,
          statusLabel: 'Executing task — browsing Reminders & due dates...',
          toolCall: {
            tool: 'reminders.scan',
            args: { lists: ['Work', 'University', 'Personal'], includeOverdue: true },
            decision: 'allow',
            riskTier: 'safe',
            reversible: true,
            durationMs: 240,
          },
          previewTitle: `${targetEnv} — Reminders`,
          previewLines: [
            '[!] Today 17:00  Overwatch Demo Deck',
            '[!] Nov 27 23:59  Hackathon Submission Portal',
            '[ ] Nov 29 14:00  API Security Review',
            '[ ] Dec 01 10:00  Sprint Retrospective',
          ],
        },
        {
          delayMs: 3000,
          statusLabel: 'Executing task — checking calendar conflicts...',
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
        `Here are your upcoming deadlines from Calendar & Reminders (checked on ${targetEnv}):\n\n` +
        `1. **Today · 5:00 PM** — Overwatch Demo Deck *(Urgent)*\n` +
        `2. **Thu, Nov 27 · 11:59 PM** — Hackathon Submission Portal\n` +
        `3. **Sat, Nov 29 · 2:00 PM** — API Security Review\n` +
        `4. **Mon, Dec 1 · 10:00 AM** — Sprint Retrospective\n\n` +
        `All 4 reminders are clear of calendar conflicts. Want me to set a 1-hour alert before today's 5:00 PM deadline?`,
    }
  }

  // 2. Website / Application Form Filling
  if (lower.includes('website') || lower.includes('form') || lower.includes('fill') || lower.includes('apply') || lower.includes('http')) {
    const urlMatch = prompt.match(/https?:\/\/[^\s]+/i)
    const targetUrl = urlMatch ? urlMatch[0] : 'https://portal.example.org/apply'

    return {
      title: 'Application Form Assistant',
      steps: [
        {
          delayMs: 3300,
          statusLabel: `Executing task — opening ${targetUrl} on ${targetEnv}...`,
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
            'Detected 6 input fields + 1 file upload',
          ],
        },
        {
          delayMs: 4000,
          statusLabel: 'Executing task — filling form fields from profile...',
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
          delayMs: 3000,
          statusLabel: 'Executing task — requesting submission permission...',
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
        `The MCP gateway paused right before clicking **Submit** because submitting an external form is irreversible. Tap **Approve** below to submit, or **Deny** to review it yourself.`,
    }
  }

  // 3. Python Project / Resume Building
  if (lower.includes('python') || lower.includes('resume') || lower.includes('project') || lower.includes('build') || lower.includes('code')) {
    return {
      title: 'Python Project Workspace',
      steps: [
        {
          delayMs: 3200,
          statusLabel: `Executing task — scanning Python workspace on ${targetEnv}...`,
          toolCall: {
            tool: 'files.scan_workspace',
            args: { path: '/workspace/python-project', env: targetEnv },
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
          delayMs: 4200,
          statusLabel: 'Executing task — running pytest test suite...',
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
          delayMs: 3200,
          statusLabel: 'Executing task — resuming code changes in gateway.py...',
          toolCall: {
            tool: 'files.edit',
            args: { path: 'src/agent/gateway.py', change: 'Added retry & timeout guard' },
            decision: 'allow',
            riskTier: 'safe',
            reversible: true,
            durationMs: 75,
          },
        },
      ],
      finalText:
        `Resumed your Python project on **${targetEnv}**:\n\n` +
        `• Located unfinished changes in \`src/agent/gateway.py\`\n` +
        `• Ran \`pytest tests/ -q\` — **14 passed** in 0.84s\n` +
        `• Added the async timeout guard you left off on\n\n` +
        `Next up: should I wire the CLI entrypoint in \`main.py\` or run the integration suite?`,
    }
  }

  // 4. Dynamic fallback for ANY custom prompt the user types
  const snippet = prompt.trim().slice(0, 44) + (prompt.trim().length > 44 ? '...' : '')
  const toolByRole: Record<AgentRole, { tool: string; args: Record<string, unknown>; preview: string[] }> = {
    general: {
      tool: 'desktop.inspect',
      args: { query: snippet, env: targetEnv },
      preview: [`Target: ${targetEnv}`, `Task: "${snippet}"`, 'Checking active windows & context...', '[OK] Action plan ready'],
    },
    code: {
      tool: 'terminal.run',
      args: { cmd: `overwatch-exec --task "${snippet}"`, env: targetEnv },
      preview: [`$ overwatch-exec --task "${snippet}"`, 'Inspecting workspace files...', '[OK] Checks completed (exit 0)'],
    },
    research: {
      tool: 'browser.search',
      args: { q: snippet, sources: 4 },
      preview: [`Searching: "${snippet}"`, 'Fetched 4 primary sources', 'Cross-checking citations...', '[OK] Summary compiled'],
    },
  }
  const picked = toolByRole[role]

  return {
    title: snippet || 'New Task',
    steps: [
      {
        delayMs: 3400,
        statusLabel: `Executing task on ${targetEnv}...`,
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
        delayMs: 3000,
        statusLabel: 'Executing task — verifying MCP policy & audit log...',
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
      `Executed **"${prompt.trim()}"** under **${role === 'general' ? 'General Work' : role === 'code' ? 'Code' : 'Research'}** mode on **${targetEnv}**.\n\n` +
      `All MCP gateway checks passed. Tell me if you want to refine the output, run another step, or switch to ${env.mode === 'desktop' ? 'an isolated VM' : 'Remote Desktop'}.`,
  }
}
