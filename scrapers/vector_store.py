#!/usr/bin/env python3
"""
Vector Store Local — Patrones de scraping con embeddings semánticos.
Usa FAISS (CPU) + embeddings hash locales (sin API externa).
Persistencia en JSONL + índice FAISS binario.
"""

import json
import os
import pickle
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    import faiss
    _HAS_FAISS = True
except ImportError:
    _HAS_FAISS = False
    faiss = None


VECTOR_STORE_DIR = str(Path(_PROJECT_ROOT) / "vector_store")
PATTERNS_FILE = str(Path(VECTOR_STORE_DIR) / "patterns.jsonl")
INDEX_FILE = str(Path(VECTOR_STORE_DIR) / "patterns.faiss")
META_FILE = str(Path(VECTOR_STORE_DIR) / "patterns_meta.pkl")

EMBEDDING_MODEL = "hash/local-768"
EMBEDDING_DIM = 768
SIMILARITY_THRESHOLD = 0.70  # Umbral base (se ajusta dinámicamente)
FITNESS_WEIGHT = 0.4
SIMILARITY_WEIGHT = 0.6
MIN_FITNESS = 0.05
MIN_FAILURES_FOR_PRUNE = 3


class Pattern:
    """Patrón de extracción aprendido."""

    def __init__(
        self,
        url_pattern: str,
        target: str,
        strategy: str,
        selector_logic: str,
        domain: str,
        embedding: Optional[List[float]] = None,
        pattern_id: Optional[str] = None,
    ):
        self.id = pattern_id or str(uuid.uuid4())
        self.url_pattern = url_pattern
        self.target = target
        self.strategy = strategy
        self.selector_logic = selector_logic
        self.domain = domain
        self.embedding = embedding or [0.0] * EMBEDDING_DIM
        self.fitness = 0.5  # Wilson score inicial
        self.success_count = 0
        self.failure_count = 0
        self.last_succeeded_at: Optional[str] = None
        self.last_failed_at: Optional[str] = None
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.created_at

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "url_pattern": self.url_pattern,
            "target": self.target,
            "strategy": self.strategy,
            "selector_logic": self.selector_logic,
            "domain": self.domain,
            "embedding": self.embedding,
            "fitness": round(self.fitness, 4),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_succeeded_at": self.last_succeeded_at,
            "last_failed_at": self.last_failed_at,
            "created_at": self.created_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Pattern":
        p = cls(
            url_pattern=data["url_pattern"],
            target=data["target"],
            strategy=data["strategy"],
            selector_logic=data["selector_logic"],
            domain=data["domain"],
            embedding=data.get("embedding"),
            pattern_id=data["id"],
        )
        p.fitness = data.get("fitness", 0.5)
        p.success_count = data.get("success_count", 0)
        p.failure_count = data.get("failure_count", 0)
        p.last_succeeded_at = data.get("last_succeeded_at")
        p.last_failed_at = data.get("last_failed_at")
        p.created_at = data.get("created_at", p.created_at)
        p.updated_at = data.get("updated_at", p.updated_at)
        return p

    def composite_score(self, query_embedding: List[float]) -> float:
        """Score combinado: similarity * 0.6 + fitness * 0.4"""
        if not self.embedding or not query_embedding:
            return 0.0
        # Cosine similarity
        a = np.array(self.embedding, dtype=np.float32)
        b = np.array(query_embedding, dtype=np.float32)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        sim = float(np.dot(a, b) / (norm_a * norm_b))
        return SIMILARITY_WEIGHT * sim + FITNESS_WEIGHT * self.fitness

    def record_success(self):
        self.success_count += 1
        self.last_succeeded_at = datetime.now(timezone.utc).isoformat()
        self._update_fitness()
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def record_failure(self):
        self.failure_count += 1
        self.last_failed_at = datetime.now(timezone.utc).isoformat()
        self._update_fitness()
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def _update_fitness(self):
        """Wilson score interval lower bound + time decay."""
        n = self.success_count + self.failure_count
        if n == 0:
            self.fitness = 0.5
            return
        p = self.success_count / n
        z = 1.96  # 95% confidence
        denom = 1 + z**2 / n
        centre = p + z**2 / (2 * n)
        adjust = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
        wilson_lower = (centre - adjust) / denom
        # Time decay: reduce fitness if no recent success
        decay = 1.0
        if self.last_succeeded_at:
            try:
                last = datetime.fromisoformat(self.last_succeeded_at.replace("Z", "+00:00"))
                days_old = (datetime.now(timezone.utc) - last).days
                decay = max(0.5, 1.0 - days_old * 0.02)  # -2% por día, min 0.5
            except Exception:
                pass
        self.fitness = max(0.0, min(1.0, wilson_lower * decay))

    def should_prune(self) -> bool:
        return self.fitness < MIN_FITNESS and self.failure_count >= MIN_FAILURES_FOR_PRUNE


class VectorStore:
    """Almacén vectorial de patrones con FAISS."""

    def __init__(self, store_dir: str = VECTOR_STORE_DIR):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.patterns: Dict[str, Pattern] = {}
        self.index: Optional[Any] = None
        self._pattern_ids: List[str] = []  # aligned with index
        self._load()

    def _load(self):
        # Cargar patrones desde JSONL
        if Path(PATTERNS_FILE).exists():
            with open(PATTERNS_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        p = Pattern.from_dict(data)
                        self.patterns[p.id] = p
                    except Exception:
                        continue

        # Cargar/reconstruir índice FAISS
        if _HAS_FAISS and Path(INDEX_FILE).exists() and Path(META_FILE).exists():
            try:
                self.index = faiss.read_index(INDEX_FILE)
                with open(META_FILE, "rb") as f:
                    self._pattern_ids = pickle.load(f)
                # Verificar consistencia
                if self.index.ntotal != len(self._pattern_ids):
                    print(f"⚠️ Vector store: índice FAISS ({self.index.ntotal}) ≠ meta ({len(self._pattern_ids)}), reconstruyendo")
                    self._rebuild_index()
            except Exception as e:
                print(f"⚠️ Vector store: error cargando índice: {e}, reconstruyendo")
                self._rebuild_index()
        else:
            self._rebuild_index()

    def _rebuild_index(self):
        if not _HAS_FAISS:
            print("⚠️ FAISS no instalado, vector search deshabilitado")
            self.index = None
            self._pattern_ids = []
            return

        embeddings = []
        ids = []
        for pid, p in self.patterns.items():
            if p.embedding and any(v != 0.0 for v in p.embedding):
                embeddings.append(p.embedding)
                ids.append(pid)

        if not embeddings:
            self.index = faiss.IndexFlatIP(EMBEDDING_DIM)  # Inner product = cosine si normalizados
            self._pattern_ids = []
            return

        vecs = np.array(embeddings, dtype=np.float32)
        # Normalizar para cosine similarity via inner product
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vecs = vecs / norms

        self.index = faiss.IndexFlatIP(EMBEDDING_DIM)
        self.index.add(vecs)
        self._pattern_ids = ids
        self._save_index()

    def _save_index(self):
        if _HAS_FAISS and self.index is not None:
            try:
                faiss.write_index(self.index, INDEX_FILE)
                with open(META_FILE, "wb") as f:
                    pickle.dump(self._pattern_ids, f)
            except Exception as e:
                print(f"⚠️ Error guardando índice FAISS: {e}")

    def _save_pattern(self, pattern: Pattern):
        """Append a JSONL (append-only log)."""
        try:
            with open(PATTERNS_FILE, "a") as f:
                f.write(json.dumps(pattern.to_dict()) + "\n")
        except Exception as e:
            print(f"⚠️ Error guardando pattern JSONL: {e}")

    def add_or_update(self, pattern: Pattern) -> str:
        """Añade o actualiza pattern, regenera embedding si cambió."""
        is_new = pattern.id not in self.patterns
        self.patterns[pattern.id] = pattern
        self._save_pattern(pattern)
        self._rebuild_index()  # Simple: rebuild completo (patrones ~cientos, rápido)
        return pattern.id

    def get_embedding(self, text: str) -> Optional[List[float]]:
        """Genera embedding determinístico hash-based (sin LLM externo)."""
        import hashlib
        dim = EMBEDDING_DIM
        vec = [0.0] * dim
        tokens = (text or "").lower().split()
        for tok in tokens:
            digest = hashlib.md5(tok.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]

    def search(
        self,
        query_text: str,
        domain: Optional[str] = None,
        strategy: Optional[str] = None,
        top_k: int = 5,
        threshold: float = SIMILARITY_THRESHOLD,
    ) -> List[Tuple[Pattern, float]]:
        """Búsqueda KNN + re-rank por composite score."""
        if not _HAS_FAISS or self.index is None or self.index.ntotal == 0:
            return []

        query_emb = self.get_embedding(query_text)
        if not query_emb:
            return []

        # Normalizar query
        q = np.array(query_emb, dtype=np.float32).reshape(1, -1)
        q_norm = np.linalg.norm(q)
        if q_norm > 0:
            q = q / q_norm

        # FAISS search
        k = min(top_k * 3, self.index.ntotal)  # over-fetch para filtrar
        scores, indices = self.index.search(q, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            pid = self._pattern_ids[idx]
            pattern = self.patterns.get(pid)
            if not pattern:
                continue
            if domain and pattern.domain != domain:
                continue
            if strategy and pattern.strategy != strategy:
                continue
            composite = pattern.composite_score(query_emb)
            if composite >= threshold:
                results.append((pattern, composite))

        # Ordenar por composite desc
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def prune_stale(self) -> int:
        """Elimina patterns con fitness bajo y muchos fallos."""
        to_remove = [pid for pid, p in self.patterns.items() if p.should_prune()]
        for pid in to_remove:
            del self.patterns[pid]
        if to_remove:
            self._rebuild_index()
            # Re-escribir JSONL completo (simple)
            self._rewrite_jsonl()
        return len(to_remove)

    def _rewrite_jsonl(self):
        try:
            with open(PATTERNS_FILE, "w") as f:
                for p in self.patterns.values():
                    f.write(json.dumps(p.to_dict()) + "\n")
        except Exception as e:
            print(f"⚠️ Error reescribiendo JSONL: {e}")

    def stats(self) -> Dict:
        total = len(self.patterns)
        by_domain = {}
        by_strategy = {}
        fitness_sum = 0.0
        for p in self.patterns.values():
            by_domain[p.domain] = by_domain.get(p.domain, 0) + 1
            by_strategy[p.strategy] = by_strategy.get(p.strategy, 0) + 1
            fitness_sum += p.fitness
        return {
            "total_patterns": total,
            "by_domain": by_domain,
            "by_strategy": by_strategy,
            "avg_fitness": round(fitness_sum / total, 3) if total else 0.0,
            "faiss_ready": _HAS_FAISS and self.index is not None and self.index.ntotal > 0,
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dim": EMBEDDING_DIM,
        }


# Singleton
_vector_store_instance: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    global _vector_store_instance
    if _vector_store_instance is None:
        _vector_store_instance = VectorStore()
    return _vector_store_instance


if __name__ == "__main__":
    vs = get_vector_store()
    print("Vector Store Stats:", vs.stats())
    # Test embedding
    emb = vs.get_embedding("instagram.com/explore/tags/psytrance event extraction")
    print("Embedding test:", len(emb) if emb else "FAILED")
    # Test search
    results = vs.search("instagram psytrance event", domain="instagram.com", top_k=3)
    print(f"Search results: {len(results)}")
    for p, score in results:
        print(f"  {p.id[:8]} | {p.strategy} | fitness={p.fitness:.2f} | composite={score:.3f}")