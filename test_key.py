"""Quick sanity check: does our API key work?"""
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("ERROR: GEMINI_API_KEY not found in .env file")
    exit(1)

print(f"Key loaded, length: {len(api_key)} chars, starts with: {api_key[:6]}...")
print("Sending a test message to Gemini...\n")

try:
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="Reply with exactly this sentence: Hello, your key works."
    )
    print("SUCCESS! Gemini replied:")
    print(response.text)
except Exception as e:
    print("FAILED — here's the error:")
    print(f"{type(e).__name__}: {e}")
