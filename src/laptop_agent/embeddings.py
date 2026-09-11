"""Semantic vectors for retrieval, behind an injectable backend.

Keyword scoring only finds a document that reuses the asker's words. Measured on six
short documents and five paraphrased questions, the lexical index answered 1 of 5 —
three returned nothing at all, because no word overlapped — where vectors answered 5.

The model is asymmetric: a document must be embedded as a "passage" and a question as a
"query". Using one type for both quietly costs accuracy, so the two are separate calls
here rather than one convenience function.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence

# (texts, input_type) -> one vector per text. Injectable so retrieval is tested offline.
EmbedBackend = Callable[[Sequence[str], str], list[list[float]]]

DEFAULT_MODEL = "nvidia/nemotron-3-embed-1b"
# Long documents are truncated rather than chunked here: the store already holds whole
# files, and one vector for the opening of a document is a better signal than none.
MAX_CHARS = 8000


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def reciprocal_rank_fusion(rankings: Sequence[Sequence[int]], k: int = 60) -> dict[int, float]:
    """Combine rankings by rank rather than by score.

    A BM25 score and a cosine similarity are not on the same scale, and normalising them
    against each other makes the blend depend on the spread of whichever result set
    happens to be wider. RRF only reads positions, which is why it is the usual way to
    fuse a keyword list with a vector list.
    """
    fused: dict[int, float] = {}
    for ranking in rankings:
        for position, key in enumerate(ranking):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + position + 1)
    return fused


class Embedder:
    """Turns text into vectors, or reports that it cannot.

    Every method returns None rather than raising when the service is unreachable, so a
    caller can fall back to keyword scoring: losing the network should cost retrieval
    quality, not the feature.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        backend: EmbedBackend | None = None,
        timeout: int = 30,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model = model or DEFAULT_MODEL
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self._backend = backend or self._http_backend
        self._injected = backend is not None

    def available(self) -> bool:
        return self._injected or bool(self.api_key and self.base_url)

    def _http_backend(self, texts: Sequence[str], input_type: str) -> list[list[float]]:
        body = {
            "model": self.model,
            "input": list(texts),
            "input_type": input_type,
            "encoding_format": "float",
        }
        request = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.load(response)
        # The API may return the batch out of order, so trust index rather than position.
        items = sorted(payload["data"], key=lambda item: item.get("index", 0))
        return [list(item["embedding"]) for item in items]

    def _embed(self, texts: Sequence[str], input_type: str) -> list[list[float]] | None:
        wanted = [t[:MAX_CHARS] for t in texts if (t or "").strip()]
        if not wanted or not self.available():
            return None
        try:
            vectors = self._backend(wanted, input_type)
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError, TypeError):
            return None
        return vectors if len(vectors) == len(wanted) else None

    def documents(self, texts: Sequence[str]) -> list[list[float]] | None:
        return self._embed(texts, "passage")

    def document(self, text: str) -> list[float] | None:
        vectors = self._embed([text], "passage")
        return vectors[0] if vectors else None

    def query(self, text: str) -> list[float] | None:
        vectors = self._embed([text], "query")
        return vectors[0] if vectors else None
