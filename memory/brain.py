"""core/brain.py

Piper's Long-Term Vector Memory (RAG).
Uses ChromaDB for storage and SentenceTransformers for embeddings.
"""

import json
import os
import math
import threading
import datetime
import hashlib
from pathlib import Path
from typing import List, Dict, Optional

try:
    import chromadb
    from chromadb.utils import embedding_functions
except ImportError:
    chromadb = None
    embedding_functions = None

def _get_deterministic_id(text: str) -> str:
    """Creates a stable ID based on content to prevent duplicates."""
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in "".join(ch.lower() if ch.isalnum() else " " for ch in str(text or "")).split()
        if token
    }

class PiperBrain:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.db_path = data_dir / "vector_store"
        self._fallback_store_path = data_dir / "state" / "brain_fallback.json"
        self._fallback_lock = threading.Lock()
        self._fallback_entries: list[dict] = []
        self._vector_memory_available = chromadb is not None and embedding_functions is not None
        self._vector_init_lock = threading.Lock()
        self._vector_init_started = False
        self._vector_init_failed = False
        self._vector_init_error = ""
        self._vector_ready = False

        print("[Brain] Initializing Long-Term Memory...")

        self.embedding_func = None
        self.client = None
        self.collection = None
        self._fallback_entries = self._load_fallback_entries()

        if self._vector_memory_available:
            self.start_vector_warmup()
            print("[Brain] Fallback memory ready. Vector warm-up started in background.")
            print(f"[Brain] Memory Ready. Current fallback entries: {len(self._fallback_entries)}")
            return

        print("[Brain] ChromaDB unavailable. Using lightweight local fallback memory.")
        print(f"[Brain] Memory Ready. Current entries: {len(self._fallback_entries)}")

    @property
    def vector_ready(self) -> bool:
        return bool(self._vector_ready and self.collection is not None)

    @property
    def vector_warmup_pending(self) -> bool:
        return bool(
            self._vector_memory_available
            and not self.vector_ready
            and not self._vector_init_failed
        )

    def start_vector_warmup(self) -> bool:
        if not self._vector_memory_available or self.vector_ready or self._vector_init_failed:
            return False
        with self._vector_init_lock:
            if self._vector_init_started or self.vector_ready or self._vector_init_failed:
                return False
            self._vector_init_started = True
            threading.Thread(
                target=self._initialize_vector_backend,
                name="PiperBrainVectorWarmup",
                daemon=True,
            ).start()
            return True

    def _initialize_vector_backend(self) -> None:
        try:
            embedding_func = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name="all-MiniLM-L6-v2"
            )
            client = chromadb.PersistentClient(path=str(self.db_path))
            collection = client.get_or_create_collection(
                name="piper_memory",
                embedding_function=embedding_func,
                metadata={"hnsw:space": "cosine"}
            )
            with self._vector_init_lock:
                self.embedding_func = embedding_func
                self.client = client
                self.collection = collection
                self._vector_ready = True
            self._sync_fallback_to_vector()
            try:
                entry_count = int(collection.count())
                print(f"[Brain] Vector memory ready. Current entries: {entry_count}")
            except Exception:
                print("[Brain] Vector memory ready.")
        except Exception as exc:
            with self._vector_init_lock:
                self._vector_init_failed = True
                self._vector_init_error = str(exc)
            print("[Brain] Vector warm-up failed. Staying on lightweight fallback memory.")
            print(f"[Brain] Vector init error: {exc}")

    def _sync_fallback_to_vector(self) -> None:
        if self.collection is None:
            return
        with self._fallback_lock:
            entries = list(self._fallback_entries)
        if not entries:
            return
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict] = []
        for entry in entries:
            text = _normalize_text(entry.get("text", ""))
            if not text:
                continue
            ids.append(str(entry.get("id") or f"mem_{_get_deterministic_id(text)}"))
            documents.append(text)
            metadatas.append(dict(entry.get("metadata") or {}))
        if not ids:
            return
        try:
            self.collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        except Exception as exc:
            print(f"[Brain] Vector fallback sync failed: {exc}")

    def _load_fallback_entries(self) -> list[dict]:
        if not self._fallback_store_path.exists():
            return []
        try:
            payload = json.loads(self._fallback_store_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        return [dict(item) for item in payload if isinstance(item, dict)]

    def _save_fallback_entries(self) -> None:
        self._fallback_store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = list(self._fallback_entries)
        self._fallback_store_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _fallback_remember(self, text: str, metadata: Dict | None = None, doc_id: str | None = None) -> None:
        normalized = _normalize_text(text)
        if not normalized:
            return
        meta = dict(metadata or {})
        resolved_id = str(doc_id or f"{meta.get('type', 'mem')}_{_get_deterministic_id(normalized)}")
        entry = {
            "id": resolved_id,
            "text": normalized,
            "metadata": meta,
        }
        with self._fallback_lock:
            for index, existing in enumerate(self._fallback_entries):
                existing_id = str(existing.get("id") or "").strip()
                existing_text = _normalize_text(existing.get("text", ""))
                if existing_id == resolved_id or existing_text == normalized:
                    self._fallback_entries[index] = entry
                    self._save_fallback_entries()
                    return
            self._fallback_entries.append(entry)
            self._save_fallback_entries()

    def _fallback_recall(self, query: str, n_results: int = 10) -> List[Dict]:
        normalized_query = _normalize_text(query)
        if not normalized_query:
            return []

        query_tokens = _tokenize(normalized_query)
        if not query_tokens:
            return []

        ranked: list[dict] = []
        with self._fallback_lock:
            entries = list(self._fallback_entries)

        for entry in entries:
            text = _normalize_text(entry.get("text", ""))
            if not text:
                continue
            metadata = dict(entry.get("metadata") or {})
            text_tokens = _tokenize(text)
            if not text_tokens:
                continue

            overlap = len(query_tokens & text_tokens)
            contains_query = normalized_query.lower() in text.lower()
            if overlap <= 0 and not contains_query:
                continue

            score = overlap / max(len(query_tokens), 1)
            if contains_query:
                score = max(score, 1.0)

            distance = max(0.0, 1.0 - min(score, 1.0))
            adjusted_distance = distance

            date_str = str(metadata.get("date") or "").strip()
            if date_str:
                try:
                    mem_date = datetime.datetime.strptime(date_str, "%b %d, %Y")
                    age_days = (datetime.datetime.now() - mem_date).days
                    time_constant = 30.0
                    decay = 0.7 * (1 - math.exp(-age_days / time_constant))
                    adjusted_distance = distance + decay
                except Exception:
                    adjusted_distance = distance

            ranked.append(
                {
                    "text": text,
                    "metadata": metadata,
                    "distance": distance,
                    "adjusted_distance": adjusted_distance,
                }
            )

        ranked = [item for item in ranked if item["adjusted_distance"] <= 0.8]
        ranked.sort(key=lambda item: item["adjusted_distance"])
        return ranked[:n_results]

    def remember(self, text: str, metadata: Dict = None, doc_id: str = None):
        """Store a memory with Hybrid Deduplication (Semantic + Hash)."""
        if not text or not text.strip():
            return
            
        # 1. Normalize Text
        text = _normalize_text(text)
        meta = metadata or {}
        self._fallback_remember(text, metadata=meta, doc_id=doc_id)

        if not self._vector_memory_available:
            return
        if self.collection is None:
            self.start_vector_warmup()
            return
        
        # 2. SEMANTIC DEDUPLICATION
        # Check if we already have a very similar memory
        try:
            results = self.collection.query(
                query_texts=[text],
                n_results=1,
                include=['distances'] 
            )
            
            if results and results['distances'] and results['distances'][0]:
                distance = results['distances'][0][0]
                
                # If very close match (distance < 0.2), UPDATE existing entry
                if distance < 0.2:
                    existing_id = results['ids'][0][0]
                    self.collection.upsert(
                        ids=[existing_id],
                        documents=[text],
                        metadatas=[meta]
                    )
                    return # Done. Updated existing memory.
        except Exception as e:
            print(f"[Brain] Deduplication check failed: {e}")

        # 3. CREATE NEW (Force Deterministic ID)
        # This handles race conditions for identical text.
        doc_id = f"{meta.get('type', 'mem')}_{_get_deterministic_id(text)}"
            
        try:
            self.collection.upsert(
                ids=[doc_id],
                documents=[text],
                metadatas=[meta]
            )
        except Exception as e:
            print(f"[Brain] Error remembering: {e}")

    def recall(self, query: str, n_results: int = 10) -> List[Dict]:
        """Search memory for relevant info with Exponential Decay."""
        if not query:
            return []

        if not self._vector_memory_available:
            return self._fallback_recall(query, n_results=n_results)
        if self.collection is None:
            self.start_vector_warmup()
            return self._fallback_recall(query, n_results=n_results)
            
        try:
            candidate_pool = max(n_results * 4, 20)
            results = self.collection.query(
                query_texts=[query],
                n_results=candidate_pool,
                include=['documents', 'metadatas', 'distances']
            )
            
            memories = []
            if results and results['documents']:
                for i, doc in enumerate(results['documents'][0]):
                    meta = results['metadatas'][0][i] if results['metadatas'] else {}
                    dist = results['distances'][0][i] if results['distances'] else 0
                    
                    # --- EXPONENTIAL DECAY MATH ---
                    date_str = meta.get('date')
                    if date_str:
                        try:
                            mem_date = datetime.datetime.strptime(date_str, "%b %d, %Y")
                            age_days = (datetime.datetime.now() - mem_date).days
                            
                            # Max Penalty 0.7, Time Constant 30 days
                            time_constant = 30.0
                            decay = 0.7 * (1 - math.exp(-age_days / time_constant))
                            
                            adjusted_dist = dist + decay
                        except:
                            adjusted_dist = dist
                    else:
                        adjusted_dist = dist

                    memories.append({
                        "text": doc,
                        "metadata": meta,
                        "distance": dist,
                        "adjusted_distance": adjusted_dist
                    })
            
            # Filter and Sort
            valid_memories = [m for m in memories if m['adjusted_distance'] <= 0.8]
            valid_memories.sort(key=lambda x: x['adjusted_distance'])
            
            return valid_memories[:n_results]
            
        except Exception as e:
            print(f"[Brain] Recall error: {e}")
            return self._fallback_recall(query, n_results=n_results)

# Cache brains by their backing data dir so multiple user silos can coexist
# without sharing one global vector-memory instance.
_brains: dict[str, PiperBrain] = {}


def get_brain(data_dir: Path = None) -> PiperBrain:
    if data_dir is None:
        from config import CFG

        data_dir = CFG.DATA_DIR
    resolved = str(Path(data_dir).resolve())
    brain = _brains.get(resolved)
    if brain is None:
        brain = PiperBrain(Path(data_dir))
        _brains[resolved] = brain
    return brain
