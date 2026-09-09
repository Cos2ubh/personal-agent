/**
 * WhatsApp bridge for the personal agent.
 *
 * What it does:
 *   1. Connects to WhatsApp Web via QR code scan (one-time; session persists)
 *   2. Receives incoming messages → forwards to FastAPI POST /chat
 *   3. Sends the agent's response text back to the same WhatsApp chat
 *   4. Polls GET /outbox every 60s for proactive messages (morning briefing, etc.)
 *      and sends them to the configured OWNER_NUMBER
 *
 * Setup:
 *   cd whatsapp && npm install
 *   node bridge.js
 *   (scan the QR code with a dedicated WhatsApp account — not your main number)
 *
 * Environment variables (set in .env at project root, or export them):
 *   AGENT_API_URL   URL of the FastAPI server  (default: http://localhost:8502)
 *   OWNER_NUMBER    Your WhatsApp number in full international format, no +/spaces
 *                   e.g. 919876543210 for +91 98765 43210
 *   BRIDGE_PORT     Port this bridge script listens on (default: not needed — it's
 *                   event-driven via whatsapp-web.js; the outbox poll is internal)
 *
 * How the approval flow works:
 *   - If the agent needs to perform a destructive action, it responds with a text
 *     message explaining the action and asking "Reply YES to approve or NO to cancel"
 *   - You reply YES or NO from WhatsApp — that reply is forwarded as the next message
 *   - The agent resumes the loop after your decision
 *
 * Session state:
 *   - whatsapp-web.js saves the auth session to whatsapp/.wwebjs_auth/
 *   - The fastapi server keeps conversation history in memory (resets on restart)
 */

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const axios  = require('axios');
const path   = require('path');
const fs     = require('fs');

// ── Config ────────────────────────────────────────────────────────────────

// Load .env from project root
const envPath = path.join(__dirname, '..', '.env');
if (fs.existsSync(envPath)) {
  require('fs').readFileSync(envPath, 'utf8')
    .split('\n')
    .forEach(line => {
      const [key, ...vals] = line.trim().split('=');
      if (key && !key.startsWith('#') && !(key in process.env)) {
        process.env[key] = vals.join('=').replace(/^["']|["']$/g, '');
      }
    });
}

const API_URL      = (process.env.AGENT_API_URL || 'http://localhost:8502').replace(/\/$/, '');
const OWNER_NUMBER = process.env.OWNER_NUMBER || '';   // e.g. "919876543210"
const POLL_INTERVAL_MS = 60_000;   // 60 seconds

if (!OWNER_NUMBER) {
  console.warn('[bridge] WARNING: OWNER_NUMBER not set. Proactive push will not work.');
}

// ── Build session ID from a WhatsApp chat ID ──────────────────────────────

function sessionId(from) {
  // from looks like "919876543210@c.us" or "GROUP_ID@g.us"
  return `wa:${from}`;
}

// ── WhatsApp client ────────────────────────────────────────────────────────

const client = new Client({
  authStrategy: new LocalAuth({
    dataPath: path.join(__dirname, '.wwebjs_auth'),
  }),
  puppeteer: {
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  },
});

// Show QR code in terminal on first run
client.on('qr', qr => {
  console.log('[bridge] Scan this QR code with WhatsApp (dedicated agent number):');
  qrcode.generate(qr, { small: true });
});

client.on('authenticated', () => console.log('[bridge] Authenticated.'));
client.on('ready',         () => {
  console.log('[bridge] WhatsApp client ready.');
  startOutboxPoller();
});

client.on('auth_failure', msg => {
  console.error('[bridge] Authentication failed:', msg);
  process.exit(1);
});

// ── Incoming message handler ───────────────────────────────────────────────

client.on('message', async msg => {
  // Ignore group chats and status updates for now
  if (msg.isGroupMsg || msg.from === 'status@broadcast') return;

  const from    = msg.from;   // e.g. "919876543210@c.us"
  const text    = msg.body.trim();
  const sid     = sessionId(from);

  console.log(`[bridge] ← ${from}: ${text.slice(0, 80)}`);

  // Forward to agent API
  let responseText = '(no response)';
  try {
    const res = await axios.post(`${API_URL}/chat`, {
      session_id: sid,
      message:    text,
    }, { timeout: 60_000 });

    responseText = res.data.text || '(empty response)';
  } catch (err) {
    console.error('[bridge] API error:', err.message);
    responseText = `Sorry, something went wrong: ${err.message}`;
  }

  // Send response back
  console.log(`[bridge] → ${from}: ${responseText.slice(0, 80)}`);
  await msg.reply(responseText);
});

// ── Outbox poller (proactive messages) ────────────────────────────────────

async function startOutboxPoller() {
  if (!OWNER_NUMBER) return;

  const ownerJid = `${OWNER_NUMBER}@c.us`;
  const sid      = sessionId(ownerJid);

  setInterval(async () => {
    try {
      const res = await axios.get(`${API_URL}/outbox`, {
        params: { session_id: sid },
        timeout: 10_000,
      });
      const messages = res.data.messages || [];
      for (const text of messages) {
        console.log(`[bridge] proactive push → ${ownerJid}: ${text.slice(0, 80)}`);
        const chat = await client.getChatById(ownerJid);
        await chat.sendMessage(text);
      }
    } catch (err) {
      // Silently ignore poll errors — server might be starting up
    }
  }, POLL_INTERVAL_MS);

  console.log(`[bridge] Outbox poller active — checking every ${POLL_INTERVAL_MS / 1000}s for ${ownerJid}`);
}

// ── Start ─────────────────────────────────────────────────────────────────

console.log(`[bridge] Starting — API at ${API_URL}`);
client.initialize();
