"""Tool router: pick the few tools relevant to a request by meaning (embeddings).

Small models get confused when they see dozens of tool schemas, so each request only
gets the top-k most similar tools + a few always-on core tools + the tools used in the
previous turn (so "pause it" still finds the music tools).

Tool vectors are cached on disk keyed by a hash of the embedded text, so only tools
whose name/docstring/examples changed are re-embedded.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

import numpy as np

from thanos.config import Settings
from thanos.tools.registry import ToolRegistry

log = logging.getLogger(__name__)

CACHE_PATH = Path("~/.cache/thanos/tool_embeddings.json").expanduser()


class Embedder(Protocol):
    model: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class OllamaEmbedder:
    def __init__(self, settings: Settings) -> None:
        import ollama

        self.model = settings.embed_model
        self._client = ollama.Client(host=settings.ollama_host)
        # nomic-embed-text is trained with task prefixes; other models don't need them
        nomic = "nomic" in self.model
        self._doc_prefix = "search_document: " if nomic else ""
        self._query_prefix = "search_query: " if nomic else ""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embed(model=self.model, input=[self._doc_prefix + t for t in texts])
        return [list(v) for v in resp.embeddings]

    def embed_query(self, text: str) -> list[float]:
        resp = self._client.embed(model=self.model, input=[self._query_prefix + text])
        return list(resp.embeddings[0])


class ToolRouter:
    def __init__(
        self,
        registry: ToolRegistry,
        embedder: Embedder,
        settings: Settings,
        cache_path: Path = CACHE_PATH,
    ) -> None:
        self.registry = registry
        self.embedder = embedder
        self.top_k = settings.router_top_k
        self.always = list(settings.always_tools)
        self.cache_path = cache_path
        self._vectors: dict[str, np.ndarray] | None = None

    def select(self, user_text: str, recent: Iterable[str] = ()) -> list[str]:
        """Tool names to offer for this request. Falls back to every tool on any error."""
        try:
            ranked = self.ranked(user_text)
        except Exception as e:
            log.warning("Tool router unavailable (%s); offering all tools", e)
            return self.registry.names()
        picked = {name for name, _ in ranked[: self.top_k]}
        picked |= {n for n in [*self.always, *recent] if self.registry.get(n)}
        # Stable order (always-on first, then registry order): requests that pick similar
        # tools share a longer prompt prefix, which Ollama can reuse from its cache.
        always = [n for n in self.always if n in picked]
        rest = [n for n in self.registry.names() if n in picked and n not in always]
        return always + rest

    def ranked(self, user_text: str) -> list[tuple[str, float]]:
        vectors = self._tool_vectors()
        q = _unit(np.asarray(self.embedder.embed_query(user_text), dtype=np.float32))
        scores = [(name, float(v @ q)) for name, v in vectors.items()]
        return sorted(scores, key=lambda x: x[1], reverse=True)

    def warmup(self) -> None:
        self._tool_vectors()

    def _tool_vectors(self) -> dict[str, np.ndarray]:
        if self._vectors is not None and set(self._vectors) == set(self.registry.names()):
            return self._vectors
        cache = self._load_cache()
        keys = {
            t.name: _hash(f"{self.embedder.model}\n{t.routing_text()}") for t in self.registry.tools()
        }
        missing = [t for t in self.registry.tools() if keys[t.name] not in cache]
        if missing:
            log.info("Embedding %d tool descriptions…", len(missing))
            vecs = self.embedder.embed_documents([t.routing_text() for t in missing])
            for t, v in zip(missing, vecs, strict=True):
                cache[keys[t.name]] = v
            self._save_cache({k: cache[k] for k in keys.values()})  # drop stale entries
        self._vectors = {
            name: _unit(np.asarray(cache[key], dtype=np.float32)) for name, key in keys.items()
        }
        return self._vectors

    def _load_cache(self) -> dict[str, list[float]]:
        try:
            return json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_cache(self, cache: dict[str, list[float]]) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(cache))
        except OSError as e:
            log.warning("Could not save tool embedding cache: %s", e)


def create_router(registry: ToolRegistry, settings: Settings) -> ToolRouter | None:
    if not settings.router_enabled:
        return None
    return ToolRouter(registry, OllamaEmbedder(settings), settings)


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n else v


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]
