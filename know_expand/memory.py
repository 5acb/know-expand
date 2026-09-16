"""Memory management using Mem0 and Qdrant vector store.

Provides fallback to an in-memory MockMemory client if Qdrant is not available.
"""

import os
import logging
from pathlib import Path
from know_expand.config import Config

_logger = logging.getLogger("know_expand.memory")


class MockMemory:
    """In-memory fallback when Mem0/Qdrant is unavailable (e.g. in test runs)."""
    def __init__(self) -> None:
        self.store: dict[str, list[dict]] = {}

    def add(self, data: str, user_id: str = "default", metadata: dict | None = None) -> None:
        if user_id not in self.store:
            self.store[user_id] = []
        self.store[user_id].append({
            "text": data,
            "metadata": metadata or {},
        })
        _logger.debug("MockMemory [add]: user_id=%s data=%s", user_id, data)

    def search(self, query: str, user_id: str = "default", limit: int = 5) -> list[dict]:
        results = []
        user_mem = self.store.get(user_id, [])
        for item in user_mem:
            if query.lower() in item["text"].lower():
                results.append(item)
        if not results:
            results = user_mem[:limit]
        _logger.debug("MockMemory [search]: query=%s user_id=%s found=%d", query, user_id, len(results))
        return results


def get_memory_client(cfg: Config, run_id: str):
    """Return a Mem0 Memory client configured with Qdrant, or MockMemory as fallback."""
    try:
        from mem0 import Memory
        from qdrant_client import QdrantClient

        host = getattr(cfg.memory, "host", "localhost")
        port = getattr(cfg.memory, "port", 6333)
        collection = getattr(cfg.memory, "collection_name", "know_expand_memory")

        # Test Qdrant connection first to fail fast and fallback
        qc = QdrantClient(host=host, port=port, timeout=2.0)
        qc.get_collections()

        mem_config = {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "host": host,
                    "port": port,
                    "collection_name": collection,
                }
            }
        }

        # Check for available keys to configure Mem0's LLM
        if os.environ.get("OPENAI_API_KEY"):
            mem_config["llm"] = {
                "provider": "openai",
                "config": {
                    "model": "gpt-4o-mini",
                    "api_key": os.environ.get("OPENAI_API_KEY")
                }
            }
        elif os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            mem_config["llm"] = {
                "provider": "google",
                "config": {
                    "model": "gemini-1.5-flash",
                    "api_key": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
                }
            }
        elif os.environ.get("ANTHROPIC_API_KEY"):
            mem_config["llm"] = {
                "provider": "anthropic",
                "config": {
                    "model": "claude-3-haiku-20240307",
                    "api_key": os.environ.get("ANTHROPIC_API_KEY")
                }
            }

        memory = Memory.from_config(mem_config)
        _logger.info("Connected to Qdrant vector database via Mem0.")
        return memory
    except Exception as e:
        _logger.warning("Mem0/Qdrant unavailable (%s). Falling back to MockMemory.", e)
        return MockMemory()
