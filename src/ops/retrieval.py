from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import Field

from ops.domain import Record, Source, StrictModel


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """Offline lexical feature hashing baseline; not a learned semantic model."""

    name = "signed-feature-hash-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            vector = [0.0] * 384
            words = tokenize(text)
            features = words + [" ".join(pair) for pair in zip(words, words[1:], strict=False)]
            for word, count in Counter(features).items():
                digest = hashlib.sha256(word.encode()).digest()
                index = int.from_bytes(digest[:4], "big") % len(vector)
                vector[index] += (1 if digest[4] % 2 else -1) * (1 + math.log(count))
            norm = math.sqrt(sum(value * value for value in vector)) or 1
            result.append([value / norm for value in vector])
        return result


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self.api_key = api_key
        self.name = model
        self.input_tokens = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Fixed egress destination; no user-supplied URLs or redirects.
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.name, "input": texts},
            )
            response.raise_for_status()
            payload = response.json()
        vectors: list[list[float]] = []
        for row in sorted(payload["data"], key=lambda row: row["index"]):
            vector = [float(value) for value in row["embedding"]]
            if not vector or any(not math.isfinite(v) for v in vector):
                raise ValueError("Embedding response contains invalid vectors")
            norm = math.sqrt(sum(v * v for v in vector)) or 1
            vectors.append([value / norm for value in vector])
        if len(vectors) != len(texts) or len({len(v) for v in vectors}) != 1:
            raise ValueError("Embedding response shape mismatch")
        self.input_tokens += int(payload["usage"]["prompt_tokens"])
        return vectors


class Chunk(StrictModel):
    id: str
    record: Record
    start: int
    end: int
    content_hash: str
    embedding: list[float]


class SearchHit(StrictModel):
    chunk_id: str
    record: Record
    score: float
    vector_score: float
    keyword_score: float
    content_hash: str
    start: int
    end: int


class IndexFile(StrictModel):
    schema_version: int = 1
    embedding_model: str
    chunks: list[Chunk] = Field(max_length=10000)


class HybridRetriever:
    def __init__(
        self,
        documents: list[Record],
        embedder: Embedder | None = None,
        chunk_size: int = 1200,
        overlap: int = 150,
    ):
        if not 0 <= overlap < chunk_size:
            raise ValueError("Chunk overlap must be smaller than the chunk")
        if len(documents) > 10000:
            raise ValueError("Exact-search index limited to 10,000 documents")
        self.embedder = embedder or HashEmbedder()
        self.chunks: list[Chunk] = []
        pending: list[tuple[Record, int, int]] = []
        for document in documents:
            if document.source != Source.DOCUMENTATION:
                raise ValueError("Only documentation records can be ingested")
            if len(document.content) > 100_000:
                raise ValueError("Document exceeds ingestion size limit")
            for start in range(0, len(document.content), chunk_size - overlap):
                end = min(len(document.content), start + chunk_size)
                pending.append((document, start, end))
                if end == len(document.content):
                    break
        if len(pending) > 10000:
            raise ValueError("Exact-search index limited to 10,000 chunks")
        texts = [doc.content[start:end] for doc, start, end in pending]
        vectors = self.embedder.embed(texts) if texts else []
        for (document, start, end), vector in zip(pending, vectors, strict=True):
            digest = hashlib.sha256(document.content[start:end].encode()).hexdigest()
            identity = hashlib.sha256(
                f"{document.tenant_id}:{document.source_id}:{start}:{digest}".encode()
            ).hexdigest()[:24]
            self.chunks.append(
                Chunk(
                    id=f"chunk-{identity}",
                    record=document,
                    start=start,
                    end=end,
                    content_hash=digest,
                    embedding=vector,
                )
            )

    def save(self, path: Path) -> None:
        data = IndexFile(embedding_model=self.embedder.name, chunks=self.chunks)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path, embedder: Embedder | None = None) -> HybridRetriever:
        if path.stat().st_size > 50_000_000:
            raise ValueError("Index file exceeds policy")
        data = IndexFile.model_validate_json(path.read_text(encoding="utf-8"))
        index = cls([], embedder=embedder)
        if data.schema_version != 1 or data.embedding_model != index.embedder.name:
            raise ValueError("Index schema or embedding version mismatch; reingest documents")
        for chunk in data.chunks:
            digest = hashlib.sha256(
                chunk.record.content[chunk.start : chunk.end].encode()
            ).hexdigest()
            if digest != chunk.content_hash or any(not math.isfinite(v) for v in chunk.embedding):
                raise ValueError("Index integrity validation failed")
        index.chunks = data.chunks
        return index

    def search(
        self,
        query: str,
        *,
        service: str,
        tenant_id: str,
        metadata: dict[str, str] | None = None,
        limit: int = 5,
    ) -> list[SearchHit]:
        if not 1 <= limit <= 200 or len(query) > 8000:
            raise ValueError("Invalid retrieval query or limit")
        candidates = [
            chunk
            for chunk in self.chunks
            if chunk.record.tenant_id == tenant_id
            and chunk.record.service == service
            and all(
                str(chunk.record.attributes.get(k, "")) == v for k, v in (metadata or {}).items()
            )
        ]
        if not candidates:
            return []
        query_tokens = tokenize(query)
        query_vector = self.embedder.embed([query])[0]
        corpus = [tokenize(chunk.record.content[chunk.start : chunk.end]) for chunk in candidates]
        average_length = sum(len(tokens) for tokens in corpus) / len(corpus) or 1
        document_frequency = Counter(token for tokens in corpus for token in set(tokens))
        keywords: list[float] = []
        vectors: list[float] = []
        for chunk, tokens in zip(candidates, corpus, strict=True):
            if len(query_vector) != len(chunk.embedding):
                raise ValueError("Embedding dimension mismatch; reingest documents")
            vectors.append(sum(a * b for a, b in zip(query_vector, chunk.embedding, strict=True)))
            frequencies = Counter(tokens)
            score = 0.0
            for token in set(query_tokens):
                frequency = frequencies[token]
                idf = math.log(
                    1
                    + (len(corpus) - document_frequency[token] + 0.5)
                    / (document_frequency[token] + 0.5)
                )
                score += (
                    idf
                    * frequency
                    * 2.2
                    / (frequency + 1.2 * (0.25 + 0.75 * len(tokens) / average_length))
                )
            keywords.append(score)
        vector_rank = sorted(range(len(candidates)), key=lambda i: -vectors[i])
        keyword_rank = sorted(range(len(candidates)), key=lambda i: -keywords[i])
        fusion: dict[int, float] = {}
        for ordering in (vector_rank, keyword_rank):
            for rank, index in enumerate(ordering, start=1):
                fusion[index] = fusion.get(index, 0) + 1 / (60 + rank)
        hits = []
        for i, chunk in enumerate(candidates):
            if query_tokens and keywords[i] <= 0 and vectors[i] <= 0.1:
                continue
            # Rerank fused candidates using exact query phrase coverage.
            content = chunk.record.content[chunk.start : chunk.end].lower()
            pairs = list(zip(query_tokens, query_tokens[1:], strict=False))
            phrase_score = sum(f"{a} {b}" in content for a, b in pairs) / max(1, len(pairs))
            hits.append(
                SearchHit(
                    chunk_id=chunk.id,
                    record=chunk.record,
                    score=fusion[i] + 0.01 * phrase_score,
                    vector_score=vectors[i],
                    keyword_score=keywords[i],
                    content_hash=chunk.content_hash,
                    start=chunk.start,
                    end=chunk.end,
                )
            )
        return sorted(hits, key=lambda h: (-h.score, h.chunk_id))[:limit]


def ingest_manifest(path: Path, output: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    documents = [Record.model_validate(item) for item in data]
    index = HybridRetriever(documents)
    index.save(output)
    return len(index.chunks)
