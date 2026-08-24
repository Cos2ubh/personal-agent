# Personal Agent — Demo Storyboard

A ~90-second walkthrough that shows every core capability without feeling rushed. Meant for the recruiter-facing demo you'll attach to Founder's Office applications.

## Before you hit record

**Prep (5 min):**

1. Close everything except one browser window and one terminal
2. Launch the app: `streamlit run app.py` — leave the Streamlit tab open at http://localhost:8501
3. Confirm auth is fresh:
   - `TAVILY_API_KEY` set in `.env`
   - `/gmail-auth` completed (with the calendar scopes — re-consent if needed)
   - `/index-docs` and `/index-images` already run so searches don't stall on first index
   - Have `docs/demo_seed.md` (or similar) somewhere in your read scope with a sample marksheet-like document
4. Have a fresh browser tab open on your Gmail inbox — you'll switch to it once to show a draft landing
5. Set the terminal + Streamlit windows so both are visible in the recording — Streamlit takes 2/3 of the screen, terminal takes 1/3 for the notifier

**Recording tips:**

- Use OBS or the built-in Windows Xbox Game Bar recorder (`Win + G`)
- 1080p, 30 fps is plenty
- Record system audio + mic if you want to narrate; or record silent and add captions in post
- 90 seconds ≈ 8-10 short scenes at ~10 seconds each — don't linger

---

## The script (10 scenes, ~90 seconds)

### Scene 1 — The pitch (5s)
**On screen:** Streamlit landing page, sidebar visible with facts / scopes / audit log.
**Say/caption:** *"A local AI personal assistant that completes tasks end-to-end — not another chatbot."*

### Scene 2 — Memory is real (10s)
**Type:** `my birthday is July 15 and I'm allergic to peanuts`
**Show:** The `📌 Remembered:` chips appear. Sidebar "Facts" expander updates live.
**Say:** *"Every fact I mention gets auto-extracted and saved to persistent memory."*

### Scene 3 — Local file access, sandboxed (10s)
**Type:** `find my marksheet and summarize the key details`
**Show:** Tool-info card shows `search_documents_by_content`, then `extract_text` firing on the returned path. Final answer summarizes the doc.
**Say:** *"It searches my documents by CONTENT — not filename — and only inside folders I've allowed."*

### Scene 4 — Approval gate (15s)
**Type:** `save a note called weekend_plan.md in my project folder with three lines about my goals`
**Show:** The approval card appears with the full preview. Pause on it for 2 seconds so viewers read it. Click **Approve**.
**Say:** *"Any destructive action — writes, drafts, sends — pauses and asks. Never a silent overwrite."*

### Scene 5 — Web access with prompt-injection defense (10s)
**Type:** `search the web for the latest news on Anthropic Claude and give me the top 3 headlines`
**Show:** `web_search` + `web_fetch` fire in sequence. Final reply is 3 clean bullets.
**Say:** *"It can search and read the web. Fetched content is wrapped as untrusted data so it can't hijack the agent."*

### Scene 6 — Gmail read (10s)
**Type:** `what's in my inbox this morning? Just the top 3`
**Show:** `gmail_list_recent` fires; a short list renders inline.
**Say:** *"Read-only Gmail access via OAuth — same account I already use."*

### Scene 7 — Gmail draft with hard approval (15s)
**Type:** `reply to email #1 saying I'll get back to them by end of the week`
**Show:** `gmail_draft_reply` fires → approval card → click Approve → draft ID returned.
Then type: `send that draft`
**Show:** Hard-approval card with the actual draft body preview + SEND-word confirmation box. Type `SEND`, hit Send button.
**Switch to Gmail tab briefly** to show the message landed in Sent.
**Say:** *"Drafts get a normal approval. Sends need typing SEND explicitly. Preview shows the exact bytes going out."*

### Scene 8 — Calendar + reminders + briefing (15s)
**Click** the ☀️ **Briefing** button in the sidebar.
**Show:** Composed briefing renders — reminders due today, today's calendar events, unread email count, weather for home city (if set).
**Say:** *"One button pulls today's reality together — reminders, calendar, email, weather. Same data the morning notifier fires on."*

### Scene 9 — Background notifier proof (5s)
**Switch briefly** to the terminal to run:
```
schtasks /Run /TN PersonalAgentNotifier
```
**Show:** A Windows toast pops up.
**Say:** *"Reminders fire as native notifications even when the agent is closed."*

### Scene 10 — Close (5s)
**Switch back** to Streamlit. Show the audit log expander with the trail of every action from this session.
**Say/caption:** *"Everything ran locally. My data never left the machine. Fifteen tools, one prompt-driven interface."*

---

## Prompts library — extra material if you want a longer version

Add these one-off flourishes anywhere in the storyboard if you have extra runway:

| Prompt | What it demonstrates |
|---|---|
| `find photos of me on my graduation day` | Face recognition + CLIP working together |
| `remind me to call dad on Sunday 8pm` | Natural-language time parsing |
| `what did I ask you last week about X` | Episodic memory (past conversations) |
| `try to read my .env file` | Sensitive-file blacklist blocks it, agent explains why |
| `try to write to C:\Windows\notepad.exe` | Path-outside-scope refusal + suggestion to update /permissions |

---

## What to avoid in the video

- **Don't leak the .env**, credentials.json, or token_gmail.json path in any wide-shot terminal frame
- **Don't show your real inbox** for more than the 2 seconds needed in scene 7 — freeze the frame or use a test Gmail account if you're worried
- **Don't over-narrate the tech stack** — the point is what it DOES, not that it uses ChromaDB + Gemini + Tavily
- **Don't apologise for anything** — no "sorry the indexing is slow" or "this feature is rough". Cut those scenes.

---

## After recording

1. Trim aggressively — better 60 seconds tight than 120 loose
2. Add captions in post (accessibility + LinkedIn autoplay-muted watchers)
3. Export as MP4 H.264, 1080p, target 5-15 MB (LinkedIn plays inline under 100 MB but small = shareable)
4. Upload to:
   - LinkedIn as a post with a short caption
   - GitHub repo README as an embedded `<video>` link or animated GIF preview
   - Loom link in your resume + FO application form

## Two 15-second cuts

Also record two shortened versions for FO application snippets that don't take a full 90-second video:

- **The trust cut:** scenes 4 + 7 (approval gates + hard-SEND) — shows human-in-the-loop
- **The magic cut:** scenes 2 + 3 + 8 (memory + doc search + briefing) — shows end-to-end usefulness
