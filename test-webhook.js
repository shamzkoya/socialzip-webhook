/**
 * ============================================================
 * SOCIAL ZIP — MANUS WEBHOOK TEST SCRIPT
 * ============================================================
 * Runs a series of tests against the local webhook server.
 * Start the server first: node server.js
 *
 * Usage:
 *   node test-webhook.js               # all tests
 *   node test-webhook.js health        # health check only
 *   node test-webhook.js flat          # flat-format payload test
 *   node test-webhook.js manus         # Manus nested-format test
 *   node test-webhook.js auth          # auth enforcement test
 * ============================================================
 */

const http = require('http');

const HOST = process.env.TEST_HOST || 'localhost';
const PORT = process.env.PORT || 3000;
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET || 'socialzip-manus-2024';

// ─────────────────────────────────────────────
// HTTP helper
// ─────────────────────────────────────────────
function request(method, path, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const options = {
      hostname: HOST,
      port: PORT,
      path,
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}),
        ...headers,
      },
    };

    const req = http.request(options, (res) => {
      let raw = '';
      res.on('data', (chunk) => (raw += chunk));
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode, body: JSON.parse(raw) });
        } catch {
          resolve({ status: res.statusCode, body: raw });
        }
      });
    });

    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

// ─────────────────────────────────────────────
// Test runner helpers
// ─────────────────────────────────────────────
let passed = 0;
let failed = 0;

function assert(label, condition, detail = '') {
  if (condition) {
    console.log(`  ✅  ${label}`);
    passed++;
  } else {
    console.log(`  ❌  ${label}${detail ? ' — ' + detail : ''}`);
    failed++;
  }
}

function section(title) {
  console.log(`\n${'─'.repeat(54)}`);
  console.log(`  ${title}`);
  console.log('─'.repeat(54));
}

// ─────────────────────────────────────────────
// TEST PAYLOADS
// ─────────────────────────────────────────────

// Flat format — test / direct integration
const flatPayload = {
  email: 'test-flat@example.com',
  seoScore: 72,
  facebookScore: 45,
  instagramScore: 60,
  industry: 'Retail',
  websiteUrl: 'https://example.com',
  topPriorities: 'Fix meta tags\nImprove page speed',
  quickWins: 'Add Google Business Profile',
};

// Manus nested format — ghlWebhookTrigger.ts production payload
const manusPayload = {
  contact: {
    email: 'test-manus@example.com',
    contact_id: '',           // leave blank; server will look up by email
    first_name: 'Jane',
    last_name: 'Smith',
    company_name: 'Acme Corp',
    phone: '+27 82 000 0000',
    website: 'https://acme.co.za',
  },
  audit_scores: {
    seo_score: '68',
    facebook_score: '51',
    instagram_score: '74',
    overall_score: '64',
  },
  funnel: {
    stage: 'MID',
    lowest_score_channel: 'Facebook',
    lowest_score: '51',
    biggest_opportunity: 'Facebook Ads optimisation',
  },
  insights: {
    top_priorities: ['Optimise meta descriptions', 'Improve page speed'],
    quick_wins: 'Claim and verify Google Business Profile',
  },
  social_media: {
    instagram_handle: '@acmecorp',
    facebook_url: 'https://facebook.com/acmecorp',
    website: 'https://acme.co.za',
  },
  assessment: { industry: 'E-commerce' },
};

// ─────────────────────────────────────────────
// TESTS
// ─────────────────────────────────────────────

async function testHealth() {
  section('1. Health Check  GET /');
  const res = await request('GET', '/');
  assert('HTTP 200', res.status === 200, `got ${res.status}`);
  assert('status = running', res.body.status === 'running', JSON.stringify(res.body));
  assert('service name present', typeof res.body.service === 'string');
  assert('endpoints listed', res.body.endpoints && res.body.endpoints.webhook);
}

async function testFlatFormat() {
  section('2. Flat payload  POST /webhook/test');
  const res = await request('POST', '/webhook/test', flatPayload);
  // The server will attempt a real GHL call; we only validate the request layer
  // If GHL is unreachable we still verify the server processed the payload shape
  const ok = res.status === 200 || res.status === 500;
  assert('Reached webhook handler (2xx or GHL error)', ok, `got ${res.status}`);
  if (res.status === 200) {
    assert('success = true', res.body.success === true);
    assert('contactId returned', typeof res.body.contactId === 'string');
    assert('funnelStage returned', ['TOP', 'MID', 'BOTTOM'].includes(res.body.funnelStage));
    assert('overallScore returned', typeof res.body.overallScore === 'number');
  } else {
    console.log(`  ℹ️   GHL unreachable — server-side error: ${res.body.error}`);
  }
}

async function testManusFormat() {
  section('3. Manus nested payload  POST /webhook/test');
  const res = await request('POST', '/webhook/test', manusPayload);
  const ok = res.status === 200 || res.status === 500;
  assert('Reached webhook handler (2xx or GHL error)', ok, `got ${res.status}`);
  if (res.status === 200) {
    assert('success = true', res.body.success === true);
    assert('funnelStage returned', ['TOP', 'MID', 'BOTTOM'].includes(res.body.funnelStage));
  } else {
    console.log(`  ℹ️   GHL unreachable — server-side error: ${res.body.error}`);
  }
}

async function testMissingFields() {
  section('4. Validation — missing required fields');

  // Missing email AND contactId
  const noEmail = await request('POST', '/webhook/test', {
    seoScore: 70, facebookScore: 50, instagramScore: 60,
  });
  assert('400 when email missing', noEmail.status === 400, `got ${noEmail.status}`);

  // Missing scores
  const noScores = await request('POST', '/webhook/test', {
    email: 'test@example.com',
  });
  assert('400 when scores missing', noScores.status === 400, `got ${noScores.status}`);

  // Empty body
  const empty = await request('POST', '/webhook/test', {});
  assert('400 for empty payload', empty.status === 400, `got ${empty.status}`);
}

async function testAuth() {
  section('5. Authentication  POST /webhook/audit-complete');

  // No secret → 401
  const noSecret = await request('POST', '/webhook/audit-complete', flatPayload);
  assert('401 without secret', noSecret.status === 401, `got ${noSecret.status}`);

  // Wrong secret → 401
  const badSecret = await request('POST', '/webhook/audit-complete', flatPayload, {
    'x-webhook-secret': 'wrong-secret',
  });
  assert('401 with wrong secret', badSecret.status === 401, `got ${badSecret.status}`);

  // Correct secret header → passes auth (may fail at GHL level = 500, not 401)
  const goodHeader = await request('POST', '/webhook/audit-complete', flatPayload, {
    'x-webhook-secret': WEBHOOK_SECRET,
  });
  assert('auth passes with correct x-webhook-secret', goodHeader.status !== 401, `got ${goodHeader.status}`);

  // Bearer token → passes auth
  const bearer = await request('POST', '/webhook/audit-complete', flatPayload, {
    'Authorization': `Bearer ${WEBHOOK_SECRET}`,
  });
  assert('auth passes with Bearer token', bearer.status !== 401, `got ${bearer.status}`);
}

async function test404() {
  section('6. 404 for unknown routes');
  const res = await request('GET', '/unknown-path');
  assert('404 for unknown GET', res.status === 404, `got ${res.status}`);
  const res2 = await request('POST', '/not/a/route', {});
  assert('404 for unknown POST', res2.status === 404, `got ${res2.status}`);
}

// ─────────────────────────────────────────────
// MAIN
// ─────────────────────────────────────────────
async function main() {
  const filter = process.argv[2];

  console.log('\n╔══════════════════════════════════════════════════════╗');
  console.log('║   Social Zip — Webhook Test Suite                   ║');
  console.log(`║   Target: http://${HOST}:${PORT}${' '.repeat(Math.max(0, 34 - HOST.length - String(PORT).length))}║`);
  console.log('╚══════════════════════════════════════════════════════╝');

  try {
    if (!filter || filter === 'health')  await testHealth();
    if (!filter || filter === 'flat')    await testFlatFormat();
    if (!filter || filter === 'manus')   await testManusFormat();
    if (!filter || filter === 'missing') await testMissingFields();
    if (!filter || filter === 'auth')    await testAuth();
    if (!filter || filter === '404')     await test404();
  } catch (err) {
    console.error('\n❌  Test runner error (is the server running?):', err.message);
    process.exit(1);
  }

  console.log('\n' + '═'.repeat(54));
  console.log(`  Results: ${passed} passed, ${failed} failed`);
  console.log('═'.repeat(54) + '\n');

  if (failed > 0) process.exit(1);
}

main();
