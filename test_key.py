"""Quick sanity check: does the Anthropic API key work?"""
import os
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()
api_key = os.getenv("ANTHROPIC_API_KEY")

if not api_key:
    print("ERROR: ANTHROPIC_API_KEY not found in .env file")
    raise SystemExit(1)

print(f"Key loaded, length: {len(api_key)} chars, starts with: {api_key[:8]}...")
print("Sending a test message to Claude...\n")

try:
    client = Anthropic()   # reads ANTHROPIC_API_KEY from env
    response = client.messages.create(
        model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        max_tokens=64,
        messages=[{
            "role": "user",
            "content": "Reply with exactly this sentence: Hello, your key works.",
        }],
    )
    print("SUCCESS! Claude replied:")
    print(response.content[0].text)
except Exception as e:
    print("FAILED — here's the error:")
    print(f"{type(e).__name__}: {e}")
