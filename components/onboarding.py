"""
Onboarding flow — shown on first run (before the chat UI).

Steps:
  1. Set vault master passphrase (or skip if vault already exists)
  2. Check Google OAuth (Gmail/Calendar) — prompt /gmail-auth if missing
  3. Quick preference capture (name, home city, WhatsApp number)

Returns True once the user has completed (or skipped) all steps.
State is stored in st.session_state.onboarding_done so it only runs once
per Streamlit session.
"""

from __future__ import annotations
import streamlit as st
from tools.vault import vault_is_initialized, unlock_vault, has_passphrase


def _step_vault() -> bool:
    """Step 1: vault passphrase. Returns True when done."""
    st.subheader("🔐 Step 1 of 3 — Credential Vault")
    st.markdown(
        "Your agent can store passwords and credentials **encrypted on your machine**. "
        "Set a master passphrase to unlock the vault. You'll be asked for this once "
        "each time you start the agent."
    )

    if vault_is_initialized():
        if has_passphrase():
            st.success("Vault already unlocked for this session.")
            return True
        pw = st.text_input("Vault passphrase", type="password", key="onb_vault_pw")
        if st.button("Unlock vault", key="onb_unlock"):
            result = unlock_vault(pw)
            if "unlocked" in result.lower():
                st.success(result)
                return True
            else:
                st.error(result)
    else:
        st.info(
            "No vault exists yet. Create one by setting a passphrase, or skip "
            "for now — you can set it up later with the vault_unlock tool."
        )
        pw = st.text_input("New vault passphrase", type="password", key="onb_new_pw")
        pw2 = st.text_input("Confirm passphrase", type="password", key="onb_new_pw2")
        col1, col2 = st.columns(2)
        if col1.button("Create vault", key="onb_create"):
            if not pw:
                st.error("Passphrase cannot be empty.")
            elif pw != pw2:
                st.error("Passphrases don't match.")
            else:
                from tools.vault import unlock_vault
                result = unlock_vault(pw)
                st.success(result)
                return True
        if col2.button("Skip for now", key="onb_skip_vault"):
            return True
    return False


def _step_google() -> bool:
    """Step 2: Google OAuth. Returns True when done or skipped."""
    st.subheader("📧 Step 2 of 3 — Google Account")

    try:
        from tools.gmail import is_authenticated
        authed = is_authenticated()
    except Exception:
        authed = False

    if authed:
        st.success("Google account connected (Gmail, Calendar, Sheets, Docs).")
        return True

    st.warning(
        "Google isn't connected yet. Run `/gmail-auth` in the terminal to link "
        "your Gmail, Calendar, Sheets, and Docs in one step."
    )
    st.code("python agent.py  # then type /gmail-auth", language="bash")
    if st.button("I've done this — continue", key="onb_google_skip"):
        return True
    return False


def _step_profile() -> bool:
    """Step 3: Quick preference capture. Always returns True (optional)."""
    st.subheader("👤 Step 3 of 3 — Quick Profile")
    st.markdown(
        "A few details let the agent personalise briefings and pre-fill booking forms. "
        "All stored locally — nothing leaves your machine."
    )

    semantic = st.session_state.get("semantic")
    if semantic is None:
        return True

    name     = st.text_input("Your name", value=semantic.get("user_name") or "", key="onb_name")
    city     = st.text_input("Home city (for weather)", value=semantic.get("user_home_city") or "", key="onb_city")
    wa_num   = st.text_input(
        "Your WhatsApp number (full intl. format, e.g. 919876543210)",
        value=semantic.get("whatsapp_number") or "",
        key="onb_wa"
    )

    if st.button("Save & start", key="onb_save"):
        if name.strip():
            semantic.set("user_name", name.strip())
        if city.strip():
            semantic.set("user_home_city", city.strip())
        if wa_num.strip():
            semantic.set("whatsapp_number", wa_num.strip())
            # Also write OWNER_NUMBER to .env if not already set
            import os
            if not os.getenv("OWNER_NUMBER"):
                from pathlib import Path
                env_path = Path(__file__).parent.parent / ".env"
                try:
                    content = env_path.read_text() if env_path.exists() else ""
                    if "OWNER_NUMBER" not in content:
                        env_path.open("a").write(f"\nOWNER_NUMBER={wa_num.strip()}\n")
                except Exception:
                    pass
        return True

    if st.button("Skip", key="onb_skip_profile"):
        return True

    return False


# ── Main onboarding entrypoint ────────────────────────────────────────────

def run_onboarding() -> bool:
    """
    Run the 3-step onboarding. Returns True when complete.
    Call at the top of the main app before rendering the chat.
    """
    ss = st.session_state

    if ss.get("onboarding_done"):
        return True

    step = ss.get("onboarding_step", 1)

    st.markdown("## Welcome to your Personal Agent")
    st.markdown(
        "Let's get you set up in 3 quick steps. "
        "You can always change these later by talking to the agent."
    )
    st.divider()

    if step == 1:
        done = _step_vault()
        if done:
            ss.onboarding_step = 2
            st.rerun()

    elif step == 2:
        done = _step_google()
        if done:
            ss.onboarding_step = 3
            st.rerun()

    elif step == 3:
        done = _step_profile()
        if done:
            ss.onboarding_done = True
            st.rerun()

    return False


def is_first_run() -> bool:
    """True if this is the very first time the app runs (no memory.db yet)."""
    from pathlib import Path
    db = Path(__file__).parent.parent / "data" / "memory.db"
    return not db.exists()
