# CLAUDE.md

## Project Overview

**Social Zip Manus Audit Webhook Server** - A lightweight Node.js webhook server that receives social media audit results from the Manus platform (lp.socialzip.co.za) and syncs them into GoHighLevel (GHL) CRM. When an audit completes, it updates contact custom fields, manages tags, moves pipeline opportunities, and enrolls contacts in automated email sequences.

## Tech Stack

- **Runtime**: Node.js (>=16.0.0)
- **Language**: Vanilla JavaScript (no TypeScript, no transpilation)
- **Framework**: None - uses built-in `http` and `https` modules only
- **Dependencies**: Zero npm dependencies
- **External API**: GoHighLevel (GHL) CRM via `services.leadconnectorhq.com`

## Project Structure

```
socialzip-webhook/
├── server.js          # Entire application (single-file monolith)
├── package.json       # Project metadata and scripts
└── CLAUDE.md          # This file
```

This is intentionally a minimal, single-file server. Do not split it into multiple files unless the codebase grows significantly.

## Commands

- `npm start` - Start the server (production)
- `npm run dev` - Start the server (development, same as start)
- `npm test` - Run test script (note: `test-webhook.js` is referenced but not in repo)

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PORT` | No | `3000` | Server listen port |
| `WEBHOOK_SECRET` | No | `socialzip-manus-2024` | Secret for webhook auth |
| `GHL_API_KEY` | No | Hardcoded fallback | GoHighLevel API key |
| `SKIP_AUTH` | No | - | Set to skip auth checks (testing) |

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/` | No | Health check - returns service status |
| `POST` | `/webhook/audit-complete` | Yes (`x-webhook-secret` or `Authorization` header) | Main webhook - processes audit results |
| `POST` | `/webhook/test` | No | Test webhook - same logic, no auth required |
| `OPTIONS` | `*` | No | CORS preflight |

## Webhook Processing Flow

1. Parse and validate incoming JSON payload
2. Normalize payload (supports both flat and Manus nested formats)
3. Look up GHL contact by `contactId` or `email`
4. Calculate derived fields (overall score, funnel stage, lowest/highest channels)
5. Update GHL contact custom fields with all audit scores
6. Remove `no_response` tag, add `lead_audited` tag
7. Move opportunity to "Audit Sent" stage in Leads pipeline
8. Enroll contact in Day 3 email workflow (auto-chains to Day 7 and Day 14)

## Payload Formats

The server accepts two payload formats:

**Flat format** (from test scripts):
```json
{
  "email": "...",
  "seoScore": 72,
  "facebookScore": 45,
  "instagramScore": 68
}
```

**Manus nested format** (from `ghlWebhookTrigger.ts`):
```json
{
  "contact": { "email": "...", "contact_id": "..." },
  "audit_scores": { "seo_score": "72", "facebook_score": "45", "instagram_score": "68" },
  "funnel": { "stage": "MID", "lowest_score_channel": "Facebook" },
  "insights": { "top_priorities": [...], "quick_wins": [...] }
}
```

## Code Conventions

- **No external dependencies** - keep it that way; use only Node.js built-in modules
- **Single-file architecture** - all logic lives in `server.js`
- **Async/await** with Promise-based wrappers for the `https` module
- **camelCase** for JavaScript variables and function names
- **snake_case** in Manus payload fields (normalized on ingestion)
- **Console logging** with emoji indicators for status tracking
- **Defensive coding** - null checks, fallback defaults, try-catch around non-critical operations (e.g., tag removal, workflow enrollment)
- **GHL IDs are hardcoded** in the `CONFIG` object at the top of `server.js` - workflow IDs, pipeline IDs, custom field IDs are all constants

## Key Configuration (CONFIG object)

All GHL integration IDs are in the `CONFIG` object at the top of `server.js`:
- `GHL_LOCATION_ID` - The GHL location
- `WORKFLOWS` - Day 3/7/14 email sequence workflow IDs
- `PIPELINE_ID` / `AUDIT_SENT_STAGE_ID` - Pipeline stage for post-audit
- `CUSTOM_FIELDS` - Maps 15 audit fields to GHL custom field IDs

## Deployment

Designed for any Node.js host: Railway, Render, Vercel, etc. No build step required - just `node server.js`.

## Important Notes

- The GHL API key has a hardcoded fallback in `server.js` - prefer setting via `GHL_API_KEY` env var in production
- CORS is configured to allow all origins (`*`)
- The `/webhook/test` endpoint intentionally bypasses auth for development use
- Funnel stage logic: score >= 75 = TOP, >= 60 = MID, < 60 = BOTTOM
