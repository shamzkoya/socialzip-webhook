/**
 * ============================================================
 * SOCIAL ZIP - MANUS AUDIT WEBHOOK SERVER
 * ============================================================
 * This server receives audit results from Manus (lp.socialzip.co.za)
 * and automatically:
 *   1. Finds/creates the GHL contact
 *   2. Updates all custom fields with audit scores
 *   3. Adds tag "lead_audited"
 *   4. Enrolls the contact in the Day 3 email sequence
 *      (which auto-chains to Day 7 → Day 14)
 *
 * DEPLOY TO: Railway / Render / Vercel / Any Node.js host
 * ============================================================
 */

const https = require('https');
const http = require('http');

// ============================================================
// CONFIGURATION
// ============================================================
const CONFIG = {
  PORT: process.env.PORT || 3000,
  WEBHOOK_SECRET: process.env.WEBHOOK_SECRET || 'socialzip-manus-2024',

  // GHL Settings
  GHL_API_KEY: process.env.GHL_API_KEY || 'pit-f82acd70-4352-4da6-ae0b-88b873724d83',
  GHL_LOCATION_ID: 'T3m7W3pB3ukKW1DzDa6i',

  // GHL Workflow IDs
  WORKFLOWS: {
    DAY_3: '5db7bc46-b455-4b3d-9a0a-62443662334f',   // Day 3 - Case Study Email
    DAY_7: 'a21c9b65-14aa-45e5-9cd6-78e1ac2077a0',   // Day 7 Value-Add Email
    DAY_14: '1a8777e5-063e-40b6-8a64-ac0896cad10d',  // Day 14 - Final Touch Email
  },

  // GHL Custom Field IDs
  CUSTOM_FIELDS: {
    SEO_SCORE:           'rFvHhndhXRHaBSV0BIM8',
    FACEBOOK_SCORE:      'Uo0D0EMsM0rMTUohrSXC',
    INSTAGRAM_SCORE:     'VerCgl2l0DmS1jsMMWsK',
    OVERALL_SCORE:       'cHsJcB2LHYduijBVsk4Z',
    LOWEST_SCORE_CHANNEL:'nAroEarsNdutE9vIrxmw',
    LOWEST_SCORE:        '4hVsWH4iLZSR4ycxHKog',
    HIGHEST_SCORE_AREA:  '63EVMnAhnW1cn0KBVvBk',
    BIGGEST_OPPORTUNITY: 'WfCjQ2NDVp77MBrnVHvC',
    FUNNEL_STAGE:        'VcX9vNH2pQ1lFWLbAHMF',
    TOP_PRIORITIES:      'lj6FQDUx9qoie4R8h4dw',
    QUICK_WINS:          'NPUzYwjdnS4zH7Cjac3y',
    INDUSTRY:            'DD1oULtKHf1LyKIxQnAr',
    WEBSITE_URL:         'voyh3Ckdd4uUxzU874DJ',
    INSTAGRAM_HANDLE:    '2rMjOmSKN4o6DVHSor1m',
    FACEBOOK_PAGE_URL:   'OxXe7upAp3vha7xzcu76',
  }
};

// ============================================================
// GHL API HELPER
// ============================================================
function ghlRequest(method, path, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const options = {
      hostname: 'services.leadconnectorhq.com',
      path: path,
      method: method,
      headers: {
        'Authorization': `Bearer ${CONFIG.GHL_API_KEY}`,
        'Version': '2021-07-28',
        'Content-Type': 'application/json',
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {})
      }
    };

    const req = https.request(options, (res) => {
      let responseData = '';
      res.on('data', chunk => responseData += chunk);
      res.on('end', () => {
        try {
          const parsed = JSON.parse(responseData);
          if (res.statusCode >= 400) {
            reject(new Error(`GHL API ${res.statusCode}: ${JSON.stringify(parsed)}`));
          } else {
            resolve(parsed);
          }
        } catch (e) {
          resolve({ raw: responseData, status: res.statusCode });
        }
      });
    });

    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

// ============================================================
// GHL CONTACT OPERATIONS
// ============================================================

// Find contact by email in GHL
async function findContactByEmail(email) {
  // Use query parameter to search contacts (GHL v2 API)
  const result = await ghlRequest('GET',
    `/contacts/?locationId=${CONFIG.GHL_LOCATION_ID}&query=${encodeURIComponent(email)}&limit=10`
  );
  if (!result.contacts || result.contacts.length === 0) return null;

  // Find exact email match
  const exactMatch = result.contacts.find(c =>
    c.email && c.email.toLowerCase() === email.toLowerCase()
  );
  return exactMatch || result.contacts[0];
}

// Update contact custom fields with audit scores
async function updateContactFields(contactId, auditData) {
  const customFields = [];

  const fieldMap = {
    seoScore:           CONFIG.CUSTOM_FIELDS.SEO_SCORE,
    facebookScore:      CONFIG.CUSTOM_FIELDS.FACEBOOK_SCORE,
    instagramScore:     CONFIG.CUSTOM_FIELDS.INSTAGRAM_SCORE,
    overallScore:       CONFIG.CUSTOM_FIELDS.OVERALL_SCORE,
    lowestScoreChannel: CONFIG.CUSTOM_FIELDS.LOWEST_SCORE_CHANNEL,
    lowestScore:        CONFIG.CUSTOM_FIELDS.LOWEST_SCORE,
    highestScoreArea:   CONFIG.CUSTOM_FIELDS.HIGHEST_SCORE_AREA,
    biggestOpportunity: CONFIG.CUSTOM_FIELDS.BIGGEST_OPPORTUNITY,
    funnelStage:        CONFIG.CUSTOM_FIELDS.FUNNEL_STAGE,
    topPriorities:      CONFIG.CUSTOM_FIELDS.TOP_PRIORITIES,
    quickWins:          CONFIG.CUSTOM_FIELDS.QUICK_WINS,
    industry:           CONFIG.CUSTOM_FIELDS.INDUSTRY,
    websiteUrl:         CONFIG.CUSTOM_FIELDS.WEBSITE_URL,
    instagramHandle:    CONFIG.CUSTOM_FIELDS.INSTAGRAM_HANDLE,
    facebookPageUrl:    CONFIG.CUSTOM_FIELDS.FACEBOOK_PAGE_URL,
  };

  for (const [key, fieldId] of Object.entries(fieldMap)) {
    if (auditData[key] !== undefined && auditData[key] !== null) {
      customFields.push({ id: fieldId, value: String(auditData[key]) });
    }
  }

  const updateBody = { customFields };

  // Also update standard fields if present
  if (auditData.companyName) updateBody.companyName = auditData.companyName;
  if (auditData.website) updateBody.website = auditData.website;

  console.log(`  Updating ${customFields.length} custom fields for contact ${contactId}`);
  return await ghlRequest('PUT', `/contacts/${contactId}`, updateBody);
}

// Add tag to contact
async function addTag(contactId, tag) {
  return await ghlRequest('POST', `/contacts/${contactId}/tags`, { tags: [tag] });
}

// Remove tag from contact (to prevent duplicate enrollments)
async function removeTag(contactId, tag) {
  try {
    return await ghlRequest('DELETE', `/contacts/${contactId}/tags`, { tags: [tag] });
  } catch (e) {
    console.log(`  Note: Could not remove tag "${tag}" - may not exist`);
  }
}

// Enroll contact in a workflow
async function enrollInWorkflow(contactId, workflowId) {
  // GHL requires timezone offset format: 2021-06-23T03:30:00+02:00
  const now = new Date();
  const tzOffset = -now.getTimezoneOffset();
  const sign = tzOffset >= 0 ? '+' : '-';
  const pad = (n) => String(Math.floor(Math.abs(n))).padStart(2, '0');
  const eventStartTime = now.toISOString().slice(0, 19) + sign + pad(tzOffset / 60) + ':' + pad(tzOffset % 60);

  return await ghlRequest('POST',
    `/contacts/${contactId}/workflow/${workflowId}`,
    { eventStartTime }
  );
}

// Remove contact from a workflow (to stop old generic sequence)
async function removeFromWorkflow(contactId, workflowId) {
  try {
    return await ghlRequest('DELETE', `/contacts/${contactId}/workflow/${workflowId}`);
  } catch (e) {
    console.log(`  Note: Could not remove from workflow ${workflowId}`);
  }
}

// ============================================================
// CALCULATE FUNNEL STAGE (if not provided by Manus)
// ============================================================
function calculateFunnelStage(overallScore) {
  if (overallScore >= 75) return 'TOP';
  if (overallScore >= 60) return 'MID';
  return 'BOTTOM';
}

function calculateLowestChannel(seoScore, facebookScore, instagramScore) {
  const scores = { SEO: seoScore, Facebook: facebookScore, Instagram: instagramScore };
  return Object.entries(scores).sort((a, b) => a[1] - b[1])[0][0];
}

function calculateHighestChannel(seoScore, facebookScore, instagramScore) {
  const scores = { SEO: seoScore, Facebook: facebookScore, Instagram: instagramScore };
  return Object.entries(scores).sort((a, b) => b[1] - a[1])[0][0];
}

// ============================================================
// NORMALIZE PAYLOAD
// Handles BOTH formats:
//   1) Flat format (test-webhook.js / manus-integration.ts sends this)
//   2) Manus nested format (ghlWebhookTrigger.ts sends this)
// ============================================================
function normalizePayload(raw) {
  // Detect Manus nested format by presence of contact/audit_scores/funnel keys
  if (raw.contact || raw.audit_scores || raw.funnel) {
    console.log('  Detected Manus nested payload format — normalizing...');
    const contact     = raw.contact      || {};
    const scores      = raw.audit_scores || {};
    const funnel      = raw.funnel       || {};
    const insights    = raw.insights     || {};
    const social      = raw.social_media || {};
    const assessment  = raw.assessment   || {};

    return {
      // Contact identification
      email:            contact.email      || '',
      contactId:        contact.contact_id || undefined,
      firstName:        contact.first_name || '',
      lastName:         contact.last_name  || '',
      companyName:      contact.company_name || '',
      phone:            contact.phone      || '',
      websiteUrl:       contact.website    || social.website || '',
      instagramHandle:  social.instagram_handle || '',
      facebookPageUrl:  social.facebook_url     || '',
      industry:         assessment.industry     || '',

      // Scores (Manus sends strings, convert to numbers)
      seoScore:         Number(scores.seo_score)       || 0,
      facebookScore:    Number(scores.facebook_score)  || 0,
      instagramScore:   Number(scores.instagram_score) || 0,
      overallScore:     Number(scores.overall_score)   || 0,

      // Funnel / personalisation
      funnelStage:         funnel.stage                  || '',
      lowestScoreChannel:  funnel.lowest_score_channel   || '',
      lowestScore:         Number(funnel.lowest_score)   || undefined,
      biggestOpportunity:  funnel.biggest_opportunity    || '',

      // Insights (Manus stores as arrays OR strings)
      topPriorities: Array.isArray(insights.top_priorities)
        ? insights.top_priorities.join('\n')
        : (insights.top_priorities || ''),
      quickWins: Array.isArray(insights.quick_wins)
        ? insights.quick_wins.join('\n')
        : (insights.quick_wins || ''),
    };
  }

  // Already flat format — return as-is
  return raw;
}

// ============================================================
// MAIN WEBHOOK HANDLER
// ============================================================
async function handleAuditWebhook(rawPayload) {
  // Normalise regardless of which format Manus (or our test script) sends
  const payload = normalizePayload(rawPayload);

  console.log('\n=== PROCESSING AUDIT WEBHOOK ===');
  console.log('Contact:', payload.email || payload.contactId);
  console.log('Scores - SEO:', payload.seoScore, '| FB:', payload.facebookScore, '| IG:', payload.instagramScore);

  // 1. Find GHL contact
  let contact = null;

  if (payload.contactId) {
    // Direct contact ID provided (most reliable)
    console.log('  Using provided contactId:', payload.contactId);
    contact = { id: payload.contactId };
  } else if (payload.email) {
    console.log('  Looking up contact by email:', payload.email);
    contact = await findContactByEmail(payload.email);
  }

  if (!contact) {
    throw new Error(`Contact not found for email: ${payload.email}`);
  }

  const contactId = contact.id;
  console.log('  Found contact ID:', contactId);

  // 2. Calculate derived fields if not provided
  const overallScore = payload.overallScore ||
    Math.round((payload.seoScore + payload.facebookScore + payload.instagramScore) / 3);

  const funnelStage = payload.funnelStage || calculateFunnelStage(overallScore);

  const lowestScoreChannel = payload.lowestScoreChannel ||
    calculateLowestChannel(payload.seoScore, payload.facebookScore, payload.instagramScore);

  const highestScoreArea = payload.highestScoreArea ||
    calculateHighestChannel(payload.seoScore, payload.facebookScore, payload.instagramScore);

  const lowestScore = payload.lowestScore || Math.min(
    payload.seoScore, payload.facebookScore, payload.instagramScore
  );

  // Build enriched audit data
  const auditData = {
    ...payload,
    overallScore,
    funnelStage,
    lowestScoreChannel,
    highestScoreArea,
    lowestScore,
  };

  // 3. Update GHL contact custom fields with all audit scores
  console.log('  Updating contact fields...');
  await updateContactFields(contactId, auditData);
  console.log('  ✅ Contact fields updated');

  // 4. Remove old "no_response" tag if exists, add "lead_audited" tag
  console.log('  Adding tag: lead_audited');
  await removeTag(contactId, 'no_response');
  await addTag(contactId, 'lead_audited');
  console.log('  ✅ Tag added: lead_audited');

  // 5. Remove from generic sequence (Day 3/7/14 with old generic templates)
  // This prevents duplicate emails if contact was already in the old sequence
  // Uncomment these if needed:
  // await removeFromWorkflow(contactId, CONFIG.WORKFLOWS.DAY_3);
  // await removeFromWorkflow(contactId, CONFIG.WORKFLOWS.DAY_7);
  // await removeFromWorkflow(contactId, CONFIG.WORKFLOWS.DAY_14);

  // 6. Enroll in Day 3 personalized workflow
  // The Day 3 workflow chains automatically to Day 7 → Day 14
  console.log('  Enrolling in Day 3 personalized sequence...');
  try {
    await enrollInWorkflow(contactId, CONFIG.WORKFLOWS.DAY_3);
    console.log('  ✅ Enrolled in Day 3 workflow');
  } catch (e) {
    console.log('  ⚠️  Day 3 enrollment failed (contact may already be enrolled):', e.message);
  }

  console.log('=== WEBHOOK PROCESSED SUCCESSFULLY ===\n');

  return {
    success: true,
    contactId,
    funnelStage,
    lowestScoreChannel,
    overallScore,
    message: `Contact enrolled in personalized email sequence`
  };
}

// ============================================================
// HTTP SERVER
// ============================================================
const server = http.createServer(async (req, res) => {
  // CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, x-webhook-secret, Authorization');

  if (req.method === 'OPTIONS') {
    res.writeHead(200);
    res.end();
    return;
  }

  // Health check
  if (req.method === 'GET' && req.url === '/') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      status: 'running',
      service: 'Social Zip - Manus Audit Webhook',
      endpoints: {
        webhook: 'POST /webhook/audit-complete',
        test: 'POST /webhook/test'
      }
    }));
    return;
  }

  // Main webhook endpoint
  if (req.method === 'POST' && (req.url === '/webhook/audit-complete' || req.url === '/webhook/test')) {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', async () => {
      try {
        // Parse body
        const payload = JSON.parse(body);
        console.log('\n📥 Received webhook from Manus:');
        console.log(JSON.stringify(payload, null, 2));

        // Optional: Verify webhook secret
        const secret = req.headers['x-webhook-secret'] || req.headers['authorization'];
        if (CONFIG.WEBHOOK_SECRET && secret !== CONFIG.WEBHOOK_SECRET &&
            secret !== `Bearer ${CONFIG.WEBHOOK_SECRET}`) {
          // Only enforce in production, not in test mode
          if (!process.env.SKIP_AUTH && req.url !== '/webhook/test') {
            console.log('⚠️  Invalid webhook secret');
            res.writeHead(401, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'Unauthorized - invalid webhook secret' }));
            return;
          }
        }

        // Validate required fields (support both flat AND Manus nested format)
        const email = payload.email || payload.contact?.email;
        const contactId = payload.contactId || payload.contact?.contact_id;
        const hasSeoScore = payload.seoScore !== undefined || payload.audit_scores?.seo_score !== undefined;
        const hasFbScore  = payload.facebookScore !== undefined || payload.audit_scores?.facebook_score !== undefined;
        const hasIgScore  = payload.instagramScore !== undefined || payload.audit_scores?.instagram_score !== undefined;

        if (!email && !contactId) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Missing required field: email or contactId' }));
          return;
        }

        if (!hasSeoScore || !hasFbScore || !hasIgScore) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Missing required score fields: seoScore/facebookScore/instagramScore (flat) or audit_scores.seo_score etc (Manus format)' }));
          return;
        }

        // Process the webhook
        const result = await handleAuditWebhook(payload);

        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(result));

      } catch (error) {
        console.error('❌ Webhook error:', error.message);
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: error.message }));
      }
    });
    return;
  }

  // 404
  res.writeHead(404, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ error: 'Not found' }));
});

server.listen(CONFIG.PORT, () => {
  console.log(`
╔══════════════════════════════════════════════════════╗
║   Social Zip - Manus Audit Webhook Server            ║
║   Running on port ${CONFIG.PORT}                             ║
╠══════════════════════════════════════════════════════╣
║   POST /webhook/audit-complete  → Main endpoint      ║
║   POST /webhook/test            → Test (no auth)     ║
║   GET  /                        → Health check       ║
╚══════════════════════════════════════════════════════╝
  `);

  // Warn when default (hardcoded) credentials are still in use
  if (!process.env.GHL_API_KEY) {
    console.warn('⚠️  WARNING: GHL_API_KEY not set via environment — using hardcoded default. Set GHL_API_KEY before deploying to production.');
  }
  if (!process.env.WEBHOOK_SECRET) {
    console.warn('⚠️  WARNING: WEBHOOK_SECRET not set via environment — using default "socialzip-manus-2024". Set WEBHOOK_SECRET before deploying to production.');
  }
});
