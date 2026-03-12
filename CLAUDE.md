# CLAUDE.md — socialzip-webhook

## Project Overview

A Node.js webhook server that integrates **Manus audit results** with **GoHighLevel (GHL) CRM**. When an audit completes on `lp.socialzip.co.za`, this service receives the results and orchestrates CRM updates: contact field updates, tagging, opportunity pipeline moves, and email workflow enrollment.

## Architecture

**Single-file application** — all logic lives in `server.js` (~500 lines). No build step, no external dependencies (uses only Node.js built-in `http`/`https` modules).

### Request Flow

1. Receive POST to `/webhook/audit-complete` with audit scores
2. Normalize payload (supports flat and Manus nested formats)
3. Find or identify GHL contact (by `contactId` or email lookup)
4. Update 15 custom fields on the contact (scores, insights, funnel stage)
5. Add `lead_audited` tag
6. Move opportunity to "Audit Sent" stage in Leads pipeline
7. Enroll contact in Day 3 email workflow (auto-chains to Day 7 → Day 14)

### Key Functions

| Function | Purpose |
|----------|---------|
| `ghlRequest()` | GHL API HTTP helper (all external calls go through here) |
| `normalizePayload()` | Handles flat and nested payload formats |
| `findContactByEmail()` | Looks up contacts in GHL with fuzzy matching fallback |
| `updateContactFields()` | Maps audit data to 15 GHL custom fields |
| `moveOpportunityToAuditSent()` | Moves contact's opportunity in Leads pipeline |
| `enrollInWorkflow()` | Triggers Day 3 personalized email sequence |

## Tech Stack

- **Runtime:** Node.js >= 16.0.0
- **Dependencies:** None (zero npm packages)
- **External API:** GoHighLevel v2021-07-28 (`services.leadconnectorhq.com`)

## Commands

```bash
npm start          # Run the server (node server.js)
npm run dev        # Same as start
npm test           # Run test-webhook.js (local webhook test)
```

There is no build, lint, or format step configured.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `3000` | Server listen port |
| `WEBHOOK_SECRET` | `socialzip-manus-2024` | Webhook authentication secret |
| `GHL_API_KEY` | *(hardcoded fallback)* | GoHighLevel API key |

All GHL-specific IDs (location, pipeline, stages, custom fields, workflows) are hardcoded in the `CONFIG` object at the top of `server.js`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Health check |
| `POST` | `/webhook/audit-complete` | Main webhook handler (validates secret) |
| `POST` | `/webhook/test` | Test endpoint (skips secret validation) |

## Code Conventions

- **No linting/formatting** tools configured — maintain existing style
- Two-space indentation
- camelCase for variables and functions
- Console logs use emoji prefixes for visual debugging (e.g., `✅`, `⚠️`, `❌`, `📥`)
- All async operations use `async`/`await`
- JSON request/response throughout
- CORS headers set on all responses

## Deployment

Target platforms: Railway, Render, Vercel, or any Node.js host. No Docker or CI/CD configuration. The server needs outbound HTTPS access to `services.leadconnectorhq.com` and an inbound-reachable HTTP port.

## Important Notes for AI Assistants

- **Zero dependencies** — do not add npm packages unless explicitly requested. The project intentionally uses only Node.js built-ins.
- **Single file** — all server logic is in `server.js`. Do not split into multiple files unless asked.
- **GHL IDs are environment-specific** — the hardcoded custom field IDs, pipeline IDs, and workflow IDs are tied to a specific GHL location. Do not change them without understanding the impact.
- **Dual payload support** — `normalizePayload()` handles two formats. Any changes to payload handling must preserve both.
- **Calculated fields** — `overallScore`, `funnelStage`, `lowestScoreChannel`, and `highestScoreArea` are derived from the three channel scores when not provided in the payload.
