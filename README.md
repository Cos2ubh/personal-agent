# Personal Agent

> An AI personal assistant that doesn't stop at recommendations — it completes tasks end-to-end. Books tickets, applies your personal coupons across platforms, handles payments with your one-tap authorization, manages email/calendar/reminders, and screens communications as your identified AI assistant.

## The gap most AI assistants leave

Every AI assistant today stops at *"here are your options"* or *"here's a draft — want me to send it?"* You still have to open the actual app, apply the coupons yourself, fill payment details, tap through six screens. The AI does the research. You still do the work.

## What this project does differently

End-to-end task execution with a single tap-to-authorize at moments that legally or ethically require your consent — payments, high-stakes actions, communications you'd want to review. Everything else is autonomous.

**Worked example — "book me the cheapest flight to Bangkok next Friday":**

1. Agent searches across your logged-in accounts on MakeMyTrip, Goibibo, EaseMyTrip, Ixigo (piggybacking on your existing browser sessions — no fresh logins that trigger bot detection).
2. Fetches your *personalized* coupon inventory from each platform. Meta-search sites like Kayak can't see these; only the platforms themselves know your MMT wallet balance or your Goibibo `SUPERFLY` coupon.
3. Simulates checkout on each platform to compute the *actual* final price after coupons, tokens, and stackable discounts.
4. Presents top 3 options with which coupons were applied and why one won.
5. On your approval, drives to the payment page — your phone unlocks with UPI PIN or biometric, you tap once.
6. Confirmation lands in your inbox, event is added to your calendar, flight tracker is set up automatically.

**Not just travel.** The same pattern extends to:

- Bill payments and subscription management
- Movie, event, and concert bookings
- Cab rides, food orders, restaurant reservations
- Doctor and service appointments
- Product research + purchase across e-commerce platforms

**Plus the everyday assistant work:**

- Email triage with drafted responses in your voice
- Calendar wrangling ("move my Thursday 3pm to Friday", "block 2 hours daily for deep work")
- Persistent memory that recalls context across sessions
- Context-aware reminders (time-based, location-based, presence-based)
- AI-identified receptionist mode for calls and messages you'd rather not handle personally

## Honest positioning on payments

Full autonomous access to bank accounts or cards without per-transaction consent is not legally available to any AI — regulators mandate 2FA per transaction, and every bank enforces it. Anyone promising it is either lying or committing fraud.

What is genuinely differentiated: **end-to-end task execution with a single tap-to-authorize.** The agent handles 99% of the work (searching, comparing, filling forms, applying coupons, navigating to checkout). You handle the one part that legally must be you (the PIN, OTP, or biometric).

## Who this is for

Founders, executives, doctors, senior consultants, HNW individuals — anyone whose hourly cost is meaningfully higher than the ~$200-500/month it takes to run a heavy-usage deployment. Bespoke by design, not a mass-market app.

## Scope — done vs. planned

**v0 (in progress):**
- [x] Project foundation — Python environment, secret management via `.env`, first Gemini API connectivity
- [ ] Persistent chat loop with SQLite + vector memory
- [ ] Gmail and Google Calendar integration
- [ ] Reminder scheduler with morning briefings

**v1 (weeks 5-12):**
- [ ] Cross-platform browser automation with session piggybacking
- [ ] Personalized coupon inventory and checkout simulation
- [ ] Human-in-the-loop payment confirmation UX
- [ ] Voice input/output via ElevenLabs

**v2 (longer horizon):**
- [ ] Companion phone app (Android first, iOS via MDM enrollment)
- [ ] Smartwatch tap-to-authorize confirmations
- [ ] AI-identified inbound receptionist mode for calls, WhatsApp, email
- [ ] Home-server deployment for privacy-first bespoke installs

## Tech stack (current)

- **Language:** Python 3.13
- **LLM:** Google Gemini 2.5 Flash (dev), designed to migrate to Claude 4.7 via a thin abstraction layer
- **Agent loop:** Hand-rolled from first principles, not a framework wrapper
- **Memory:** SQLite (structured) + ChromaDB (episodic, planned)
- **Integrations:** Google APIs (Gmail, Calendar) via OAuth
- **Secrets:** `.env` file loaded via `python-dotenv`

## Getting started

Requires Python 3.11+ and a Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey).

```bash
# Clone
git clone https://github.com/Cos2ubh/personal-agent.git
cd personal-agent

# Set up virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1     # Windows
source venv/bin/activate         # Mac/Linux

# Install dependencies
pip install google-genai python-dotenv

# Configure your API key
cp .env.example .env
# Edit .env and paste your Gemini API key

# Verify it works
python test_key.py
```

## Why this project exists

This is a portfolio project supporting Founder's Office job applications, and simultaneously the v0 of a longer-term bespoke AI personal-assistant business idea. Every design decision is chosen to be defensible in a technical interview — the goal isn't just to ship software, it's to understand every layer of it.

---

Built by **Kaustubh Kumar** · [LinkedIn](https://linkedin.com/in/kaustubhkumar) · [GitHub](https://github.com/Cos2ubh)
