from __future__ import annotations

import hashlib
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List

from config import data_state_path
from memory.stores import JsonDictStore

try:
    import chromadb
    from chromadb.utils import embedding_functions
except ImportError:
    chromadb = None
    embedding_functions = None

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


def _coerce_existing_path(raw_path: str | Path) -> Path | None:
    raw = str(raw_path or "").strip()
    if not raw:
        return None

    direct = Path(raw)
    if direct.exists():
        return direct

    if os.name != "nt" and len(raw) > 3 and raw[1:3] in {":\\", ":/"}:
        drive = raw[0].lower()
        suffix = raw[3:].replace("\\", "/")
        wsl_path = Path(f"/mnt/{drive}/{suffix}")
        if wsl_path.exists():
            return wsl_path

    if os.name == "nt" and raw.startswith("/mnt/") and len(raw) > 6:
        drive = raw[5]
        suffix = raw[7:].replace("/", "\\")
        win_path = Path(f"{drive.upper()}:\\{suffix}")
        if win_path.exists():
            return win_path

    candidate = Path.cwd() / raw
    if candidate.exists():
        return candidate

    return None


def _document_excerpt(text: str, *, max_chars: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 14].rstrip() + " [TRUNCATED]"


def _normalize_search_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9/._-]*")
_PAGE_SPLIT_RE = re.compile(r"(?=\[Page \d+\])")
_REFERENCE_TERM_RE = re.compile(r"\b(chapter|section|page)\s+([0-9]{1,3}[A-Za-z]?)\b", re.IGNORECASE)
_PAGE_LABEL_RE = re.compile(r"\[Page\s+(\d+)\]", re.IGNORECASE)
_CHAPTER_LABEL_RE = re.compile(r"\bChapter\s+([0-9]{1,3}[A-Za-z]?)\b", re.IGNORECASE)
_SECTION_LABEL_RE = re.compile(r"\b([A-Z]{2,}(?:-[A-Z0-9]{2,})+)\b")
_QUERY_STOPWORDS = {
    "are",
    "about",
    "again",
    "answer",
    "any",
    "ask",
    "chapter",
    "could",
    "document",
    "does",
    "for",
    "from",
    "give",
    "have",
    "of",
    "ingested",
    "injected",
    "into",
    "just",
    "manual",
    "part",
    "please",
    "said",
    "says",
    "section",
    "show",
    "summarize",
    "tell",
    "that",
    "the",
    "them",
    "this",
    "what",
    "where",
    "which",
    "with",
    "would",
}
_QUERY_TERM_SYNONYMS = {
    "rvsm": ["reduced vertical separation minimum", "pro-spo-50"],
    "wingspan": ["wing span", "wing-span", "dimensions", "principal dimensions", "general arrangement"],
    "wingtip": ["wing tip", "wing-tip"],
    "overall": ["general", "total"],
    "dimensions": ["dimension", "principal dimensions", "general arrangement", "length", "height"],
    "dimension": ["dimensions", "principal dimensions", "general arrangement", "length", "height"],
    "length": ["dimensions", "principal dimensions"],
    "height": ["dimensions", "principal dimensions"],
    "width": ["dimensions", "principal dimensions"],
}
_DOCUMENT_CHUNK_VERSION = 1
_MAX_PDF_PAGES_FOR_CHUNK_INDEX = 250
_MAX_CHUNK_INDEX_CHUNKS = 400


def _query_terms(query: str) -> List[str]:
    terms: List[str] = []
    seen: set[str] = set()

    for match in _REFERENCE_TERM_RE.finditer(str(query or "")):
        whole = match.group(0).strip().lower()
        index = match.group(2).strip().lower()
        for candidate in (whole, index):
            if candidate not in seen:
                terms.append(candidate)
                seen.add(candidate)

    for raw in _QUERY_TOKEN_RE.findall(str(query or "")):
        token = raw.strip("._-").lower()
        if not token:
            continue
        variants = [token]
        if token.endswith("s") and len(token) > 4:
            variants.append(token[:-1])
        for synonym in _QUERY_TERM_SYNONYMS.get(token, []):
            variants.append(str(synonym).strip().lower())
        normalized = _normalize_search_text(token)
        if normalized and normalized != token:
            variants.append(normalized)
        for candidate in variants:
            if len(candidate) < 3:
                continue
            if candidate in _QUERY_STOPWORDS:
                continue
            if candidate not in seen:
                terms.append(candidate)
                seen.add(candidate)
    return terms[:12]


def _document_sections(text: str) -> List[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    if "[Page " in raw:
        sections = [part.strip() for part in _PAGE_SPLIT_RE.split(raw) if part.strip()]
        if sections:
            return sections
    sections = [part.strip() for part in re.split(r"\n{2,}", raw) if part.strip()]
    return sections or [raw]


def _query_term_weight(term: str, query: str) -> int:
    raw_query = str(query or "")
    if term and re.search(rf"\b{re.escape(term.upper())}\b", raw_query):
        return 14
    if any(ch.isdigit() for ch in term):
        return 9
    if len(term) >= 7:
        return 7
    return 4


def _score_section(section: str, query: str, terms: List[str]) -> int:
    lowered = section.lower()
    normalized = _normalize_search_text(section)
    score = 0
    clean_query = str(query or "").strip().lower()
    if clean_query and clean_query in lowered:
        score += 12
    normalized_query = _normalize_search_text(clean_query)
    if normalized_query and normalized_query in normalized:
        score += 14

    for term in terms:
        count = lowered.count(term)
        weight = _query_term_weight(term, query)
        if count:
            score += weight
            score += min(count, 3)
            continue
        normalized_term = _normalize_search_text(term)
        if normalized_term and normalized_term in normalized:
            score += weight + 2
    if "table of contents" in lowered:
        score -= 28
    if "summary of highlights" in lowered:
        score -= 18
    if "preliminary pages" in lowered:
        score -= 16
    if "principal dimensions" in lowered:
        score += 24
    if "general arrangement" in lowered:
        score += 10
    if section.startswith("[Page "):
        score += 1
    return score


def _snippet_around_terms(section: str, terms: List[str], *, max_chars: int) -> str:
    clean = str(section or "").strip()
    if len(clean) <= max_chars:
        return clean

    lowered = clean.lower()
    positions = [lowered.find(term) for term in terms if term and lowered.find(term) >= 0]
    if not positions:
        return _document_excerpt(clean, max_chars=max_chars)

    hit_index = min(positions)
    start = max(0, hit_index - (max_chars // 3))
    end = min(len(clean), start + max_chars)
    if end - start < max_chars and start > 0:
        start = max(0, end - max_chars)
    snippet = clean[start:end].strip()
    if start > 0:
        snippet = "... " + snippet
    if end < len(clean):
        snippet = snippet.rstrip() + " ..."
    return snippet


def _document_query_excerpt(text: str, query: str, *, max_chars: int) -> str:
    clean_query = str(query or "").strip()
    if not clean_query:
        return _document_excerpt(text, max_chars=max_chars)

    terms = _query_terms(clean_query)
    if not terms:
        return _document_excerpt(text, max_chars=max_chars)

    ranked: List[tuple[int, int, str]] = []
    for index, section in enumerate(_document_sections(text)):
        score = _score_section(section, clean_query, terms)
        if score > 0:
            ranked.append((score, index, section))
    if not ranked:
        return _document_excerpt(text, max_chars=max_chars)

    ranked.sort(key=lambda item: (-item[0], item[1]))
    snippets: List[str] = []
    seen: set[str] = set()
    remaining = max_chars
    for _, _, section in ranked:
        if remaining < 160:
            break
        snippet_budget = remaining if not snippets else max(160, remaining // 2)
        snippet = _snippet_around_terms(section, terms, max_chars=min(snippet_budget, max_chars)).strip()
        if not snippet:
            continue
        signature = snippet[:160]
        if signature in seen:
            continue
        seen.add(signature)
        snippets.append(snippet)
        remaining -= len(snippet) + (2 if remaining != max_chars else 0)
        if len(snippets) >= 3:
            break

    if not snippets:
        return _document_excerpt(text, max_chars=max_chars)
    return "\n\n".join(snippets)


def extract_document_reference_labels(text: str, *, query: str = "", limit: int = 6) -> List[str]:
    labels: List[str] = []
    seen: set[str] = set()

    for match in _CHAPTER_LABEL_RE.finditer(str(query or "")):
        label = f"Chapter {match.group(1)}"
        if label not in seen:
            labels.append(label)
            seen.add(label)
        if len(labels) >= limit:
            return labels

    source = str(text or "")
    for regex, prefix in (
        (_PAGE_LABEL_RE, "Page "),
        (_CHAPTER_LABEL_RE, "Chapter "),
        (_SECTION_LABEL_RE, "Section "),
    ):
        for match in regex.finditer(source):
            value = match.group(1).strip()
            if prefix == "Section " and value.startswith("Page"):
                continue
            label = prefix + value
            if prefix == "Section ":
                narrower_than_existing = any(
                    existing.startswith("Section ")
                    and value.startswith(existing[len("Section "):] + "-")
                    for existing in seen
                )
                if narrower_than_existing:
                    continue
            if label in seen:
                continue
            labels.append(label)
            seen.add(label)
            if len(labels) >= limit:
                return labels
    return labels


def _extract_page_number(text: str) -> int | None:
    match = _PAGE_LABEL_RE.search(str(text or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


class DocumentMemoryManager:
    """Owns ingested document metadata and vector storage."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.index_store = JsonDictStore(data_state_path(self.data_dir, "ingested_documents.json"))
        self._client = None
        self._collection = None
        self._chunk_collection = None
        self._embedding_func = None

    def _ensure_client(self) -> None:
        if self._client is not None and self._embedding_func is not None:
            return
        if chromadb is None or embedding_functions is None:
            raise RuntimeError("chromadb is not installed.")
        vector_dir = self.data_dir / "vector_store"
        vector_dir.mkdir(parents=True, exist_ok=True)
        self._embedding_func = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        self._client = chromadb.PersistentClient(path=str(vector_dir))

    def _ensure_collection(self):
        if self._collection is not None:
            return self._collection
        self._ensure_client()
        self._collection = self._client.get_or_create_collection(
            name="piper_documents",
            embedding_function=self._embedding_func,
            metadata={"hnsw:space": "cosine"},
        )
        return self._collection

    def _ensure_chunk_collection(self):
        if self._chunk_collection is not None:
            return self._chunk_collection
        self._ensure_client()
        self._chunk_collection = self._client.get_or_create_collection(
            name="piper_document_chunks",
            embedding_function=self._embedding_func,
            metadata={"hnsw:space": "cosine"},
        )
        return self._chunk_collection

    @staticmethod
    def _document_id(path: Path) -> str:
        canonical = str(path.resolve()).lower()
        return "doc_" + hashlib.md5(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _chunk_id(doc_id: str, chunk_index: int) -> str:
        return f"{doc_id}::chunk::{chunk_index:05d}"

    @staticmethod
    def _extract_primary_section_label(text: str) -> str:
        labels = extract_document_reference_labels(text, limit=2)
        for label in labels:
            if label.startswith("Section "):
                return label[len("Section ") :]
        return ""

    @staticmethod
    def _split_text_chunks(
        text: str,
        *,
        max_chars: int = 1600,
        overlap: int = 220,
        prefix: str = "",
    ) -> List[str]:
        raw = str(text or "").strip()
        if not raw:
            return []
        if len(raw) <= max_chars:
            if prefix:
                return [f"{prefix}\n{raw}".strip()]
            return [raw]

        chunks: List[str] = []
        start = 0
        while start < len(raw):
            end = min(len(raw), start + max_chars)
            if end < len(raw):
                cut_points = [
                    raw.rfind("\n\n", start + max_chars // 2, end),
                    raw.rfind(". ", start + max_chars // 2, end),
                    raw.rfind("\n", start + max_chars // 2, end),
                    raw.rfind(" ", start + max_chars // 2, end),
                ]
                cut = max(point for point in cut_points if point != -1) if any(point != -1 for point in cut_points) else -1
                if cut > start:
                    end = cut + 1
            piece = raw[start:end].strip()
            if piece:
                if prefix and not piece.startswith(prefix):
                    piece = f"{prefix}\n{piece}".strip()
                chunks.append(piece)
            if end >= len(raw):
                break
            start = max(end - overlap, start + 1)
        return chunks

    def _build_document_chunks(
        self,
        *,
        doc_id: str,
        text: str,
        base_meta: Dict[str, Any],
        document_type: str,
    ) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []

        if document_type == "pdf":
            sections = _document_sections(text)
            for page_chunk_index, section in enumerate(sections):
                page_match = _PAGE_LABEL_RE.search(section)
                page_number = int(page_match.group(1)) if page_match else None
                body = section
                prefix = ""
                if page_match:
                    prefix = page_match.group(0)
                    body = section[page_match.end() :].strip()
                segment_texts = self._split_text_chunks(body or section, prefix=prefix)
                for segment in segment_texts:
                    chunk_index = len(chunks)
                    chunk_meta = dict(base_meta)
                    chunk_meta.update(
                        {
                            "document_id": doc_id,
                            "chunk_index": chunk_index,
                            "chunk_kind": "page",
                        }
                    )
                    if page_number is not None:
                        chunk_meta["page_number"] = page_number
                    section_label = self._extract_primary_section_label(segment)
                    if section_label:
                        chunk_meta["section_label"] = section_label
                    chunks.append(
                        {
                            "id": self._chunk_id(doc_id, chunk_index),
                            "content": segment,
                            "metadata": chunk_meta,
                        }
                    )
            return chunks

        segment_texts = self._split_text_chunks(text)
        for segment in segment_texts:
            chunk_index = len(chunks)
            chunk_meta = dict(base_meta)
            chunk_meta.update(
                {
                    "document_id": doc_id,
                    "chunk_index": chunk_index,
                    "chunk_kind": document_type,
                }
            )
            section_label = self._extract_primary_section_label(segment)
            if section_label:
                chunk_meta["section_label"] = section_label
            chunks.append(
                {
                    "id": self._chunk_id(doc_id, chunk_index),
                    "content": segment,
                    "metadata": chunk_meta,
                }
            )
        return chunks

    def _backfill_chunks_if_needed(
        self,
        *,
        doc_id: str,
        meta: Dict[str, Any],
        document_text: str | None = None,
    ) -> Dict[str, Any]:
        if int(meta.get("chunk_version") or 0) == _DOCUMENT_CHUNK_VERSION and int(meta.get("chunk_count") or 0) > 0:
            return meta

        collection = self._ensure_collection()
        chunk_collection = self._ensure_chunk_collection()
        text = str(document_text or "")
        if not text:
            fetched = collection.get(ids=[doc_id], include=["documents", "metadatas"])
            documents = fetched.get("documents") or []
            if not documents:
                return meta
            text = str(documents[0] or "")

        chunk_collection.delete(where={"document_id": doc_id})
        chunks = self._build_document_chunks(
            doc_id=doc_id,
            text=text,
            base_meta=meta,
            document_type=str(meta.get("document_type") or "text"),
        )
        if chunks:
            chunk_collection.upsert(
                ids=[item["id"] for item in chunks],
                documents=[item["content"] for item in chunks],
                metadatas=[item["metadata"] for item in chunks],
            )
        updated_meta = dict(meta)
        updated_meta["chunk_count"] = len(chunks)
        updated_meta["chunk_version"] = _DOCUMENT_CHUNK_VERSION
        index = self._load_index()
        if doc_id in index:
            index[doc_id] = updated_meta
            self._save_index(index)
        return updated_meta

    @staticmethod
    def _read_text_document(path: Path) -> str:
        data = path.read_bytes()
        if b"\x00" in data:
            raise ValueError("binary or unsupported document format")
        return data.decode("utf-8", errors="replace").strip()

    @staticmethod
    def _read_docx_text(path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            try:
                xml_bytes = archive.read("word/document.xml")
            except KeyError as exc:
                raise ValueError("DOCX document.xml was not found") from exc

        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs: List[str] = []
        for paragraph in root.findall(".//w:p", ns):
            runs = [str(node.text or "") for node in paragraph.findall(".//w:t", ns)]
            text = "".join(runs).strip()
            if text:
                paragraphs.append(text)
        return "\n\n".join(paragraphs).strip()

    @staticmethod
    def _read_pdf_text(path: Path) -> str:
        if PdfReader is None:
            raise RuntimeError("pypdf is not installed")
        reader = PdfReader(str(path))
        pages: List[str] = []
        for index, page in enumerate(reader.pages):
            text = str(page.extract_text() or "").strip()
            if not text:
                continue
            pages.append(f"[Page {index + 1}]\n{text}")
        return "\n\n".join(pages).strip()

    @classmethod
    def _read_document_text(cls, path: Path) -> tuple[str, str]:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return cls._read_pdf_text(path), "pdf"
        if suffix == ".docx":
            return cls._read_docx_text(path), "docx"
        return cls._read_text_document(path), "text"

    def _load_index(self) -> Dict[str, Dict[str, Any]]:
        data = self.index_store.load()
        return data if isinstance(data, dict) else {}

    def _save_index(self, data: Dict[str, Dict[str, Any]]) -> None:
        self.index_store.save(data)

    def ingest_path(self, raw_path: str | Path) -> Dict[str, Any]:
        path = _coerce_existing_path(raw_path)
        if path is None or not path.exists() or not path.is_file():
            return {
                "status": "FAILED",
                "summary": f"Document not found: {raw_path}",
            }

        try:
            text, document_type = self._read_document_text(path)
        except Exception as exc:
            return {
                "status": "FAILED",
                "summary": f"Could not ingest '{path.name}': {exc}",
            }

        if not text:
            return {
                "status": "FAILED",
                "summary": f"Document is empty: {path.name}",
            }

        collection = self._ensure_collection()
        doc_id = self._document_id(path)
        ingested_at = int(time.time())
        meta = {
            "name": path.name,
            "source_path": str(path),
            "ingested_at": ingested_at,
            "char_count": len(text),
            "document_type": document_type,
        }
        collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[meta],
        )
        chunks: List[Dict[str, Any]] = []
        estimated_pages = max(text.count("[Page "), 0)
        should_chunk_index = document_type != "pdf" or estimated_pages <= _MAX_PDF_PAGES_FOR_CHUNK_INDEX
        if should_chunk_index:
            chunks = self._build_document_chunks(
                doc_id=doc_id,
                text=text,
                base_meta=meta,
                document_type=document_type,
            )
        if chunks and len(chunks) <= _MAX_CHUNK_INDEX_CHUNKS:
            chunk_collection = self._ensure_chunk_collection()
            try:
                chunk_collection.delete(where={"document_id": doc_id})
            except Exception:
                pass
            chunk_collection.upsert(
                ids=[item["id"] for item in chunks],
                documents=[item["content"] for item in chunks],
                metadatas=[item["metadata"] for item in chunks],
            )
            meta["chunk_count"] = len(chunks)
            meta["chunk_version"] = _DOCUMENT_CHUNK_VERSION
        else:
            meta["chunk_count"] = 0
            meta["chunk_version"] = 0

        index = self._load_index()
        index[doc_id] = meta
        self._save_index(index)
        return {
            "status": "INGESTED",
            "summary": f"Ingested document: {path.name}",
            "document_id": doc_id,
            "metadata": meta,
        }

    def list_documents(self) -> List[Dict[str, Any]]:
        index = self._load_index()
        docs = list(index.values())
        docs.sort(key=lambda item: int(item.get("ingested_at") or 0), reverse=True)
        return docs

    def recall(self, query: str, *, limit: int = 5) -> List[Dict[str, Any]]:
        docs = self.list_documents()
        if not docs:
            return []
        collection = self._ensure_collection()
        query_text = str(query or "").strip()
        if query_text:
            terms = _query_terms(query_text)
            combined_matches: List[Dict[str, Any]] = []
            chunk_ready_docs = [
                item for item in docs
                if int(item.get("chunk_version") or 0) == _DOCUMENT_CHUNK_VERSION
                and int(item.get("chunk_count") or 0) > 0
            ]
            if chunk_ready_docs:
                chunk_collection = self._ensure_chunk_collection()
                results = chunk_collection.query(
                    query_texts=[query_text],
                    n_results=max(limit * 4, 12),
                    include=["documents", "metadatas", "distances"],
                )
                matches: List[Dict[str, Any]] = []
                documents = (results or {}).get("documents") or [[]]
                metadatas = (results or {}).get("metadatas") or [[]]
                distances = (results or {}).get("distances") or [[]]

                for index, content in enumerate(documents[0]):
                    meta = metadatas[0][index] if metadatas and metadatas[0] else {}
                    distance = float(distances[0][index] if distances and distances[0] else 0.0)
                    text = str(content or "")
                    lexical_score = _score_section(text, query_text, terms)
                    matches.append(
                        {
                            "content": text,
                            "metadata": meta or {},
                            "distance": distance,
                            "_lexical_score": lexical_score,
                        }
                    )
                combined_matches.extend(matches)

            ids = [self._document_id(Path(item["source_path"])) for item in docs if item.get("source_path")]
            fetched = collection.get(ids=ids, include=["documents", "metadatas"])
            lexical_matches: List[Dict[str, Any]] = []
            fetched_ids = fetched.get("ids") or []
            documents = fetched.get("documents") or []
            metadatas = fetched.get("metadatas") or []
            for index, doc_id in enumerate(fetched_ids):
                content = str(documents[index] or "")
                base_meta = dict(metadatas[index] or {})
                for section in _document_sections(content):
                    lexical_score = _score_section(section, query_text, terms)
                    if lexical_score <= 0:
                        continue
                    meta = dict(base_meta)
                    page_number = _extract_page_number(section)
                    if page_number is not None:
                        meta["page_number"] = page_number
                    section_label = self._extract_primary_section_label(section)
                    if section_label:
                        meta["section_label"] = section_label
                    lexical_matches.append(
                        {
                            "id": doc_id,
                            "content": section,
                            "metadata": meta,
                            "distance": 0.0,
                            "_lexical_score": lexical_score,
                        }
                    )

            if lexical_matches:
                combined_matches.extend(lexical_matches)

            if combined_matches:
                combined_matches.sort(
                    key=lambda item: (
                        -int(item.get("_lexical_score") or 0),
                        float(item.get("distance") or 0.0),
                        int((item.get("metadata") or {}).get("page_number") or 0),
                    )
                )
                trimmed: List[Dict[str, Any]] = []
                seen_signatures: set[str] = set()
                for item in combined_matches:
                    meta = dict(item.get("metadata") or {})
                    signature = "|".join(
                        [
                            str(meta.get("source_path") or meta.get("name") or ""),
                            str(meta.get("page_number") or ""),
                            str(meta.get("section_label") or meta.get("chunk_index") or ""),
                        ]
                    )
                    if signature in seen_signatures:
                        continue
                    seen_signatures.add(signature)
                    item.pop("_lexical_score", None)
                    trimmed.append(item)
                    if len(trimmed) >= limit:
                        break
                if trimmed:
                    return trimmed
            if terms:
                return []

        recent = docs[:limit]
        ids = [self._document_id(Path(item["source_path"])) for item in recent if item.get("source_path")]
        if not ids:
            return []
        fetched = collection.get(ids=ids, include=["documents", "metadatas"])
        results: List[Dict[str, Any]] = []
        fetched_ids = fetched.get("ids") or []
        documents = fetched.get("documents") or []
        metadatas = fetched.get("metadatas") or []
        for index, doc_id in enumerate(fetched_ids):
            results.append(
                {
                    "id": doc_id,
                    "content": str(documents[index] or ""),
                    "metadata": metadatas[index] or {},
                    "distance": 0.0,
                }
            )
        results.sort(key=lambda item: int(item.get("metadata", {}).get("ingested_at") or 0), reverse=True)
        return results[:limit]

    def render_prompt_hits(self, query: str, *, limit: int = 5, excerpt_chars: int = 1400) -> List[Dict[str, Any]]:
        rendered: List[Dict[str, Any]] = []
        clean_query = str(query or "").strip()
        for hit in self.recall(query, limit=limit):
            content = str(hit.get("content") or "")
            metadata = dict(hit.get("metadata") or {})
            rendered.append(
                {
                    "metadata": metadata,
                    "distance": float(hit.get("distance") or 0.0),
                    "content": _document_query_excerpt(content, clean_query, max_chars=excerpt_chars),
                }
            )
        return rendered

    def render_ui_summary(self, *, preview_chars: int = 2000) -> str:
        docs = self.list_documents()
        if not docs:
            return "No documents ingested."
        parts: List[str] = []
        hits = self.render_prompt_hits("", limit=5, excerpt_chars=preview_chars)
        preview_by_path = {
            str((item.get("metadata") or {}).get("source_path") or ""): str(item.get("content") or "")
            for item in hits
        }
        for item in docs[:20]:
            source_path = str(item.get("source_path") or "")
            ingested_at = int(item.get("ingested_at") or 0)
            char_count = int(item.get("char_count") or 0)
            preview = preview_by_path.get(source_path, "")
            parts.append(f"Name: {item.get('name', '(unknown)')}")
            parts.append(f"Path: {source_path}")
            parts.append(f"Ingested: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ingested_at))}")
            parts.append(f"Type: {item.get('document_type', 'unknown')}")
            parts.append(f"Characters: {char_count}")
            if preview:
                parts.append("")
                parts.append(preview)
            parts.append("")
            parts.append("=" * 72)
            parts.append("")
        return "\n".join(parts).rstrip()
