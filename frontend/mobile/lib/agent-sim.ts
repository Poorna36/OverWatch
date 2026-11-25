import type { AgentRole, ApprovalRequest, EnvConfig, ToolCallEvent } from './mock-data'

export interface SimulatedStep {
  delayMs: number
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
 * so any prompt (plus the 3 core demo flows) feels live and responsive.
 */
export function buildSimulation(prompt: string, role: AgentRole, env: EnvConfig): SimulatedResponse {
  const lower = prompt.toLowerCase()
  const targetEnv = env.mode === 'desktop' ? 'Remote Desktop' : `VM (${env.vmImage})`

  // 1. Calendar / Reminders / Deadlines
  if (lower.includes('calendar') || lower.includes('reminder') || lower.includes('deadline')) {
    return {
      title: 'Calendar Deadlines & Reminders',
      steps: [
        {
          delayMs: 650,
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
            'Reading events: Nov 26 – Dec 03',
            'Found 6 scheduled events, 4 reminders',
          ],
        },
        {
          delayMs: 800,
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
          delayMs: 600,
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
          delayMs: 700,
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
          delayMs: 850,
          toolCall: {
            tool: 'browser.fill_fields',
            args: {
              fullName: 'Poorn',
              email: 'demo@overwatch.dev',
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
            '✓ Full Name: Poorn',
            '✓ Email: demo@overwatch.dev',
            '✓ Project: Overwatch MCP Control Plane',
            '✓ Repo: github.com/poorn/overwatch',
            '⏸ Paused before clicking [Submit]',
          ],
        },
        {
          delayMs: 700,
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
          delayMs: 650,
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
          delayMs: 850,
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
            '✓ Environment healthy (Python 3.12)',
          ],
        },
        {
          delayMs: 700,
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
  const snippet = prompt.trim().slice(0, 44) + (prompt.trim().length > 44 ? '…' : '')
  const toolByRole: Record<AgentRole, { tool: string; args: Record<string, unknown>; preview: string[] }> = {
    general: {
      tool: 'desktop.inspect',
      args: { query: snippet, env: targetEnv },
      preview: [`Target: ${targetEnv}`, `Task: "${snippet}"`, 'Checking active windows & context...', '✓ Action plan ready'],
    },
    code: {
      tool: 'terminal.run',
      args: { cmd: `overwatch-exec --task "${snippet}"`, env: targetEnv },
      preview: [`$ overwatch-exec --task "${snippet}"`, 'Inspecting workspace files...', '✓ Checks completed (exit 0)'],
    },
    research: {
      tool: 'browser.search',
      args: { q: snippet, sources: 4 },
      preview: [`Searching: "${snippet}"`, 'Fetched 4 primary sources', 'Cross-checking citations...', '✓ Summary compiled'],
    },
  }
  const picked = toolByRole[role]

  return {
    title: snippet || 'New Task',
    steps: [
      {
        delayMs: 650,
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
        delayMs: 650,
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
