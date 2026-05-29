"""Structured memory system — Chroma (vectors) + SQLite (metadata).

Exports the primary ``MemoryStore`` class plus helper types.
"""
from .store import MemoryStore
from .models import MemoryItem, SearchResult, MEMORY_TYPES
from .embedding import Embedder, is_model_available, download_model
