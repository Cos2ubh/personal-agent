"""
Episodic memory — past conversations stored in ChromaDB (embedded, local).
Each turn (user message + agent reply) is one document, searchable by similarity.

Usage:
    from memory.episodic import EpisodicMemory
    mem = EpisodicMemory()
    mem.save_turn("what's the weather?", "I don't have live weather yet.")
    mem.recall("weather forecast")   # returns top-N relevant past turns
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path

import chromadb

CHROMA_PATH = Path(__file__).parent.parent / "data" / "chroma"


class EpisodicMemory:
    def __init__(self, chroma_path: Path = CHROMA_PATH):
        chroma_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(chroma_path))
        self._col = self._client.get_or_create_collection("conversations")

    def save_turn(self, user_msg: str, agent_reply: str):
        """Persist one conversation turn (user + agent) as a single document."""
        doc = f"User: {user_msg}\nAgent: {agent_reply}"
        self._col.add(
            documents=[doc],
            ids=[str(uuid.uuid4())],
            metadatas=[{"timestamp": datetime.now(timezone.utc).isoformat()}],
        )

    def recall(self, query: str, n: int = 3) -> list[str]:
        """
        Return up to n past turns most relevant to query.
        Returns [] if no conversations stored yet.
        """
        count = self._col.count()
        if count == 0:
            return []
        results = self._col.query(
            query_texts=[query],
            n_results=min(n, count),
        )
        return results["documents"][0]  # list of matching turn strings

    def as_prompt_block(self, query: str, n: int = 3) -> str:
        """
        Returns a formatted string of relevant past turns for system prompt injection.
        Empty string if no relevant history found.
        """
        turns = self.recall(query, n)
        if not turns:
            return ""
        block = "\n\n".join(turns)
        return f"## Relevant past conversations\n{block}"
