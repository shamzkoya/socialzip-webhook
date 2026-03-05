# CLAUDE.md — Social Zip Manus Audit Webhook

## Project Overview

This is a Node.js webhook server that bridges **Manus** (Social Zip's audit tool at lp.socialzip.co.za) with **Go High Level (GHL)** CRM. When Manus completes a marketing audit for a lead, it sends results here; this server then:

1. Looks up (or accepts by ID) the GHL contact
2. Updates all custom fields with audit scores
3. Tags the contact `lead_audited`
4. Enrolls them in a personalised Day 3 email sequence (which auto-chains to Day 7 → Day 14)

**Stack:** Pure Node.js — no framework, no dependencies, no TypeScript.

---

## Repository Structure

```
socialzip-webhook/
├── server.js       # Entire application (468 lines)
└── package.json    # Metadata & scripts only
```

This is intentionally a single-file monolith. Do not split into multiple files unless there is a compelling reason.

---

## Running Locally

```bash
node server.js          # production
npm start               # same
npm run dev             # same (no hot-reload configured)
```

Server starts on `http://localhost:3000` by default.

---

## Environment Variables

All have hardcoded defaults in the `CONFIG` object at the top of `server.js`. Override via environment for production:

| Variable | Default | Description |
|---|---|---|
| `PORT` | `3000` | HTTP listen port |
| `WEBHOOK_SECRET` | `socialzip-manus-2024` | Secret validated in `x-webhook-secret` or `Authorization: Bearer <secret>` header |
| `GHL_API_KEY` | *(hardcoded PIT key)* | Go High Level Private Integration Token |
| `SKIP_AUTH` | *(unset)* | Set to any truthy value to bypass secret check (dev only) |

> **Security note:** The GHL API key and Location ID are currently hardcoded as defaults. For any production deployment these must be injected via environment variables.

---

## API Endpoints

### `GET /`
Health check. Returns JSON with service name and available endpoints.

### `POST /webhook/audit-complete`
Main production endpoint. Requires `x-webhook-secret` (or `Authorization: Bearer <secret>`) header matching `WEBHOOK_SECRET`.

### `POST /webhook/test`
Identical behaviour but **skips authentication**. Useful for integration testing without configuring secrets.

---

## Payload Formats

The server accepts **two payload formats** and normalises them internally via `normalizePayload()`.

### Flat format (test scripts / direct integration)
```json
{
  "email": "lead@example.com",
  "seoScore": 72,
  "facebookScore": 45,
  "instagramScore": 60,
  "industry": "Retail",
  "websiteUrl": "https://example.com",
  "topPriorities": "Fix meta tags\nImprove page speed",
  "quickWins": "Add Google Business Profile"
}
```

### Manus nested format (production — `ghlWebhookTrigger.ts`)
```json
{
  "contact": {
    "email": "lead@example.com",
    "contact_id": "ghl-contact-id",
    "first_name": "Jane",
    "company_name": "Acme"
  },
  "audit_scores": {
    "seo_score": "72",
    "facebook_score": "45",
    "instagram_score": "60",
    "overall_score": "59"
  },
  "funnel": {
    "stage": "BOTTOM",
    "lowest_score_channel": "Facebook",
    "lowest_score": "45",
    "biggest_opportunity": "Facebook Ads optimisation"
  },
  "insights": {
    "top_priorities": ["Fix meta tags", "Improve page speed"],
    "quick_wins": "Add Google Business Profile"
  },
  "social_media": {
    "instagram_handle": "@acme",
    "facebook_url": "https://facebook.com/acme",
    "website": "https://example.com"
  },
  "assessment": { "industry": "Retail" }
}
```

Detection logic: if any of `contact`, `audit_scores`, or `funnel` keys are present → Manus nested format.

**Required fields** (in either format): `email` or `contactId`/`contact.contact_id`, plus all three score fields.

---

## Data Flow

```
POST /webhook/audit-complete
        │
        ▼
  Parse & validate body
        │
        ▼
  normalizePayload()        ← handles flat OR Manus nested
        │
        ▼
  Find contact in GHL       ← by contactId (preferred) or email
        │
        ▼
  Calculate derived fields  ← overallScore, funnelStage,
                               lowestScoreChannel, highestScoreArea
        │
        ▼
  updateContactFields()     ← PUT /contacts/:id with customFields[]
        │
        ▼
  removeTag('no_response')  ← cleanup
  addTag('lead_audited')
        │
        ▼
  enrollInWorkflow(DAY_3)   ← chains automatically to Day 7 → Day 14
        │
        ▼
  Return JSON result
```

---

## GHL Custom Field Mapping

Custom field IDs are defined in `CONFIG.CUSTOM_FIELDS`. The mapping from internal key → GHL field ID:

| Internal key | GHL Field | Description |
|---|---|---|
| `seoScore` | `SEO_SCORE` | SEO audit score (0–100) |
| `facebookScore` | `FACEBOOK_SCORE` | Facebook audit score |
| `instagramScore` | `INSTAGRAM_SCORE` | Instagram audit score |
| `overallScore` | `OVERALL_SCORE` | Average of all three |
| `lowestScoreChannel` | `LOWEST_SCORE_CHANNEL` | Channel name with lowest score |
| `lowestScore` | `LOWEST_SCORE` | Numeric value of lowest score |
| `highestScoreArea` | `HIGHEST_SCORE_AREA` | Channel name with highest score |
| `biggestOpportunity` | `BIGGEST_OPPORTUNITY` | Text description |
| `funnelStage` | `FUNNEL_STAGE` | `TOP` / `MID` / `BOTTOM` |
| `topPriorities` | `TOP_PRIORITIES` | Newline-separated list |
| `quickWins` | `QUICK_WINS` | Newline-separated list |
| `industry` | `INDUSTRY` | Industry name |
| `websiteUrl` | `WEBSITE_URL` | Lead's website |
| `instagramHandle` | `INSTAGRAM_HANDLE` | @handle |
| `facebookPageUrl` | `FACEBOOK_PAGE_URL` | Full URL |

---

## Derived Field Logic

When not provided by Manus, these are calculated automatically:

- **`overallScore`** — `Math.round((seo + fb + ig) / 3)`
- **`funnelStage`** — `>= 75` → `TOP`, `>= 60` → `MID`, `< 60` → `BOTTOM`
- **`lowestScoreChannel`** — channel name with minimum score
- **`highestScoreArea`** — channel name with maximum score
- **`lowestScore`** — `Math.min(seo, fb, ig)`

---

## GHL Workflows

| ID constant | Workflow | Purpose |
|---|---|---|
| `WORKFLOWS.DAY_3` | Day 3 — Case Study Email | Entry point; chains to Day 7 |
| `WORKFLOWS.DAY_7` | Day 7 — Value-Add Email | Auto-triggered by Day 3 |
| `WORKFLOWS.DAY_14` | Day 14 — Final Touch Email | Auto-triggered by Day 7 |

Only `DAY_3` is enrolled explicitly. The others fire via GHL workflow chaining.

Workflow removal calls (`removeFromWorkflow`) exist in the code but are **commented out**. Uncomment to prevent duplicate sequences when re-processing a contact.

---

## GHL API Integration

- **Base URL:** `https://services.leadconnectorhq.com`
- **Auth:** `Authorization: Bearer <GHL_API_KEY>`
- **API Version header:** `Version: 2021-07-28`
- **Transport:** Node.js built-in `https` module (no third-party HTTP client)

Enrollment requires an `eventStartTime` in ISO format with explicit timezone offset (e.g. `2024-03-05T14:30:00+02:00`) — not UTC `Z` format. This is handled in `enrollInWorkflow()`.

---

## Testing

The `test-webhook.js` file referenced in `package.json` does not exist in the repository. To test manually:

```bash
# Test endpoint (no auth required)
curl -X POST http://localhost:3000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "seoScore": 70,
    "facebookScore": 45,
    "instagramScore": 60
  }'

# Production endpoint
curl -X POST http://localhost:3000/webhook/audit-complete \
  -H "Content-Type: application/json" \
  -H "x-webhook-secret: socialzip-manus-2024" \
  -d '{ ... }'

# Health check
curl http://localhost:3000/
```

---

## Deployment

Designed for Railway / Render / Vercel or any Node.js host. Key requirements:

- Node.js >= 16.0.0
- `PORT` env var (most platforms set this automatically)
- `GHL_API_KEY` env var (never commit the real key)
- `WEBHOOK_SECRET` env var (change from default)

No build step required — just `node server.js`.

---

## Key Conventions

1. **Single file** — keep all logic in `server.js` unless a module grows beyond reason.
2. **Native Node.js only** — no npm dependencies. Use `https`, `http` built-ins.
3. **CONFIG object at top** — all configuration lives in the `CONFIG` constant; no magic strings scattered in code.
4. **Async/await throughout** — no raw `.then()` chains.
5. **Console logging with emoji markers** — `✅` success, `⚠️` warnings, `❌` errors, `📥` incoming.
6. **All scores are stored as strings in GHL** — `String(value)` is applied before all `customFields` entries.
7. **Dual-format support** — `normalizePayload()` must handle both flat and Manus nested formats; preserve this behaviour.
8. **Tag management** — always `removeTag` before `addTag` to prevent duplicates.
9. **Graceful workflow errors** — Day 3 enrollment failures are caught and logged as warnings (not fatal), since a contact may already be enrolled.
