"""
Voice input component for the Streamlit UI.

Injects a mic button using the browser's Web Speech API (no external model
or API key needed — runs entirely client-side). When the user speaks, the
transcribed text is sent back to Streamlit via st.query_params so the main
app can pick it up and inject it as the next chat message.

Usage (in app.py):
    from components.voice import render_voice_button, get_voice_transcript

    transcript = get_voice_transcript()   # check if a voice message arrived
    render_voice_button()                 # show the mic button
"""

import streamlit as st
import streamlit.components.v1 as components

_VOICE_PARAM = "voice_input"

# The mic button renders as an inline HTML component with Web Speech API JS.
# After recognition completes, it writes the transcript to the URL as a query
# param (?voice_input=...) which triggers a Streamlit rerun and lets us pick
# it up with st.query_params.

_MIC_HTML = """
<style>
  #mic-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border: 2px solid #555;
    border-radius: 20px;
    background: transparent;
    color: #ccc;
    font-size: 14px;
    cursor: pointer;
    transition: all 0.2s;
    font-family: sans-serif;
  }
  #mic-btn:hover { border-color: #888; color: #fff; }
  #mic-btn.listening { border-color: #e74c3c; color: #e74c3c; animation: pulse 1s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
  #mic-status { font-size: 12px; color: #888; margin-top: 4px; font-family: sans-serif; }
</style>

<button id="mic-btn" onclick="toggleMic()">🎤 Speak</button>
<div id="mic-status">Click mic to use voice input</div>

<script>
let recognition = null;
let listening = false;

function toggleMic() {
  if (listening) {
    recognition && recognition.stop();
    return;
  }

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    document.getElementById('mic-status').textContent =
      'Voice input not supported in this browser. Use Chrome.';
    return;
  }

  recognition = new SpeechRecognition();
  recognition.lang = 'en-IN';
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  recognition.continuous = false;

  recognition.onstart = () => {
    listening = true;
    document.getElementById('mic-btn').classList.add('listening');
    document.getElementById('mic-btn').textContent = '⏹ Stop';
    document.getElementById('mic-status').textContent = 'Listening…';
  };

  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript.trim();
    document.getElementById('mic-status').textContent = 'Heard: ' + transcript;

    // Send transcript to Streamlit via URL query param → triggers rerun
    const url = new URL(window.parent.location.href);
    url.searchParams.set('voice_input', transcript);
    window.parent.history.pushState({}, '', url.toString());
    window.parent.dispatchEvent(new Event('popstate'));
  };

  recognition.onerror = (event) => {
    document.getElementById('mic-status').textContent = 'Error: ' + event.error;
  };

  recognition.onend = () => {
    listening = false;
    document.getElementById('mic-btn').classList.remove('listening');
    document.getElementById('mic-btn').textContent = '🎤 Speak';
  };

  recognition.start();
}
</script>
"""


def render_voice_button(height: int = 90) -> None:
    """Render the mic button inside a Streamlit HTML component."""
    components.html(_MIC_HTML, height=height, scrolling=False)


def get_voice_transcript() -> str | None:
    """
    Check if a voice transcript arrived via query param.
    Returns the transcript string and clears the param, or None if absent.
    """
    params = st.query_params
    transcript = params.get(_VOICE_PARAM)
    if transcript:
        # Clear so we don't re-process on the next rerun
        params.pop(_VOICE_PARAM, None)
        return transcript.strip()
    return None
