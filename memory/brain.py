"""core/brain.py

Piper's Long-Term Vector Memory (RAG).
Uses ChromaDB for storage and SentenceTransformers for embeddings.
"""

import os
import math
import datetime
import hashlib
from pathlib import Path
from typing import List, Dict, Optional

try:
    import chromadb
    from chromadb.utils import embedding_functions
except ImportError:
    raise ImportError("Please install chromadb: pip install chromadb sentence-transformers")

def _get_deterministic_id(text: str) -> str:
    """Creates a stable ID based on content to prevent duplicates."""
    return hashlib.md5(text.encode('utf-8')).hexdigest()

class PiperBrain:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.db_path = data_dir / "vector_store"
        
        print("[Brain] Initializing Long-Term Memory...")
        
        self.embedding_func = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        
        self.client = chromadb.PersistentClient(path=str(self.db_path))
        
        self.collection = self.client.get_or_create_collection(
            name="piper_memory",
            embedding_function=self.embedding_func,
            metadata={"hnsw:space": "cosine"}
        )
        
        print(f"[Brain] Memory Ready. Current entries: {self.collection.count()}")

    def remember(self, text: str, metadata: Dict = None, doc_id: str = None):
        """Store a memory with Hybrid Deduplication (Semantic + Hash)."""
        if not text or not text.strip():
            return
            
        # 1. Normalize Text
        text = " ".join(text.strip().split())
        meta = metadata or {}
        
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
            return []

# Singleton
_brain = None

def get_brain(data_dir: Path = None) -> PiperBrain:
    global _brain
    if _brain is None:
        if data_dir is None:
            from config import CFG
            data_dir = CFG.DATA_DIR
        _brain = PiperBrain(data_dir)
    return _brain
