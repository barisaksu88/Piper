from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from config import CFG
from core.debug_tools import log_prompt_debug
from core.json_utils import parse_json_response
from memory.documents import extract_document_reference_labels
from tools.vision import generate_with_image_attachment

try:
    import fitz
except ImportError:
    fitz = None


_DEFAULT_DOCUMENT_FOCUS_PROMPT = """## ROLE
You are the Document Focus Extractor.

Your job is to compress runtime-supplied document excerpts into only the information relevant to the user's current question.

Return JSON only:
{
  "relevant_info": "short factual answer context drawn only from the excerpts",
  "references": ["Page 12", "Chapter 3", "Section PRO-SPO-50"]
}

Rules:
- Use only the supplied excerpts.
- Keep `relevant_info` concise and information-dense.
- Remove headings, boilerplate, and unrelated text.
- `references` must contain only page/chapter/section labels that are explicitly present in the excerpts or the user query.
- If the excerpts do not answer the question, return an empty `relevant_info` string.
- Do not mention tools, prompts, filesystems, or runtime behavior.
"""


@dataclass(frozen=True)
class DocumentFocusResult:
    relevant_info: str = ""
    references: List[str] = field(default_factory=list)
    source_names: List[str] = field(default_factory=list)
    used_visual_fallback: bool = False
    visual_pages: List[str] = field(default_factory=list)


_DEFAULT_DOCUMENT_VISION_PROMPT = """## ROLE
You are the PDF Page Vision Extractor.

You receive a user question and one attached PDF page image from an ingested document.

Return JSON only:
{
  "found": true,
  "answer": "direct answer from the visible page only",
  "label": "exact visible label next to the answer",
  "references": ["Page 394", "Section DSC-20-20"]
}

Rules:
- Use only text, numbers, labels, tables, and diagrams visible in the attached page image.
- Do not use outside aircraft knowledge or prior assumptions.
- If the question asks for a specific measurement, return `found: true` only when the visible label next to the value matches that measurement.
- `label` must be the exact visible label that identifies the returned value. If no matching label is visible, return `found: false`.
- If the answer is not visibly present on this page, return:
  {"found": false, "answer": "", "label": "", "references": []}
- If the page only shows a heading or table-of-contents entry without the actual value, treat that as not found.
- Keep the answer short and factual.
"""

_VISUAL_FACT_QUERY_RE = re.compile(
    r"(?i)\b("
    r"wingspan|dimension|dimensions|length|height|width|weight|weights|fuel|capacity|range|"
    r"speed|altitude|distance|clearance|pressure|temperature|angle|size|value|how many|how much"
    r")\b"
)
_NEGATIVE_ANSWER_RE = re.compile(
    r"(?i)\b("
    r"not visible|not present|not provided|not available|cannot determine|can't determine|"
    r"unable to determine|does not contain|does not provide|not detailed|not detail"
    r")\b"
)
_PDF_RENDER_SCALE = 3.0
_QUERY_LABEL_HINTS: dict[str, tuple[str, ...]] = {
    "wingspan": ("wingspan", "wing span", "span"),
    "length": ("length",),
    "height": ("height",),
    "width": ("width",),
    "clearance": ("clearance",),
    "fuel": ("fuel",),
    "capacity": ("capacity",),
    "weight": ("weight",),
    "range": ("range",),
    "speed": ("speed",),
    "altitude": ("altitude",),
    "pressure": ("pressure",),
    "temperature": ("temperature",),
}


def _load_document_focus_prompt() -> str:
    path = CFG.PROMPTS_DIR / "document_focus.txt"
    try:
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text
    except Exception:
        pass
    return _DEFAULT_DOCUMENT_FOCUS_PROMPT


def _load_document_vision_prompt() -> str:
    path = CFG.PROMPTS_DIR / "document_vision_focus.txt"
    try:
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text
    except Exception:
        pass
    return _DEFAULT_DOCUMENT_VISION_PROMPT


def _collect_source_names(document_hits: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    seen: set[str] = set()
    for hit in document_hits:
        meta = dict(hit.get("metadata") or {})
        name = str(meta.get("name") or meta.get("source_path") or "").strip()
        if not name or name in seen:
            continue
        names.append(name)
        seen.add(name)
    return names


def _collect_reference_labels(query: str, document_hits: List[Dict[str, Any]]) -> List[str]:
    labels: List[str] = []
    seen: set[str] = set()
    for hit in document_hits:
        content = str(hit.get("content") or "")
        for label in extract_document_reference_labels(content, query=query):
            if label in seen:
                continue
            labels.append(label)
            seen.add(label)
    return labels[:6]


def build_document_focus_messages(query: str, document_hits: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    excerpts: List[str] = []
    for index, hit in enumerate(document_hits[:3], start=1):
        meta = dict(hit.get("metadata") or {})
        name = str(meta.get("name") or meta.get("source_path") or f"document_{index}")
        content = str(hit.get("content") or "").strip()
        if not content:
            continue
        excerpts.append(f"[DOCUMENT {index}: {name}]\n{content}")

    user_content = "User question:\n" + str(query or "").strip()
    if excerpts:
        user_content += "\n\nDocument excerpts:\n" + "\n\n".join(excerpts)

    return [
        {"role": "system", "content": _load_document_focus_prompt()},
        {"role": "user", "content": user_content},
    ]


def _supports_visual_fallback() -> bool:
    mmproj = getattr(CFG, "MMPROJ_PATH", None)
    return fitz is not None and bool(mmproj and Path(mmproj).exists())


def _query_needs_visual_fallback(query: str, relevant_info: str) -> bool:
    lowered_query = str(query or "").strip().lower()
    if not lowered_query:
        return False
    if not _VISUAL_FACT_QUERY_RE.search(lowered_query):
        return False
    answer = str(relevant_info or "").strip()
    if not answer:
        return True
    if _NEGATIVE_ANSWER_RE.search(answer):
        return True
    if not re.search(r"\d", answer):
        return True
    return False


def _build_visual_candidates(document_hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for hit in document_hits:
        meta = dict(hit.get("metadata") or {})
        source_path = str(meta.get("source_path") or "").strip()
        page_number = meta.get("page_number")
        if not source_path or not page_number:
            continue
        if not str(source_path).lower().endswith(".pdf"):
            continue
        key = f"{source_path}|{page_number}"
        if key in seen:
            continue
        seen.add(key)
        refs = extract_document_reference_labels(str(hit.get("content") or ""), limit=4)
        page_label = f"Page {page_number}"
        if page_label not in refs:
            refs.insert(0, page_label)
        candidates.append(
            {
                "source_path": source_path,
                "page_number": int(page_number),
                "references": refs[:4],
                "name": str(meta.get("name") or Path(source_path).name),
            }
        )
        if len(candidates) >= 3:
            break
    return candidates


def _query_label_hints(query: str) -> tuple[str, ...]:
    lowered = str(query or "").lower()
    hints: List[str] = []
    for trigger, values in _QUERY_LABEL_HINTS.items():
        if trigger in lowered:
            hints.extend(values)
    deduped: List[str] = []
    seen: set[str] = set()
    for item in hints:
        value = str(item).strip().lower()
        if value and value not in seen:
            deduped.append(value)
            seen.add(value)
    return tuple(deduped)


def _visual_label_matches_query(query: str, label: str) -> bool:
    hints = _query_label_hints(query)
    if not hints:
        return True
    lowered_label = str(label or "").strip().lower()
    if not lowered_label:
        return False
    return any(hint in lowered_label for hint in hints)


def _render_pdf_page_image(pdf_path: Path, page_number: int) -> Path:
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed")
    doc = fitz.open(str(pdf_path))
    try:
        page = doc.load_page(max(page_number - 1, 0))
        pix = page.get_pixmap(matrix=fitz.Matrix(_PDF_RENDER_SCALE, _PDF_RENDER_SCALE), alpha=False)
        fd, temp_name = tempfile.mkstemp(prefix="piper-doc-page-", suffix=".png")
        os.close(fd)
        temp_path = Path(temp_name)
        pix.save(str(temp_path))
        return temp_path
    finally:
        doc.close()


def _extract_visual_answer_for_page(
    *,
    llm_client,
    query: str,
    candidate: Dict[str, Any],
    cancel_token=None,
) -> tuple[str, List[str]] | None:
    source_path = Path(str(candidate.get("source_path") or ""))
    page_number = int(candidate.get("page_number") or 0)
    refs = [str(item) for item in candidate.get("references") or [] if str(item).strip()]
    page_label = f"Page {page_number}"
    if page_label not in refs:
        refs.insert(0, page_label)

    image_path = _render_pdf_page_image(source_path, page_number)
    try:
        messages = [
            {"role": "system", "content": _load_document_vision_prompt()},
            {"role": "user", "content": f"User question:\n{str(query or '').strip()}"},
        ]
        attachment_text = (
            f"The attached image is {page_label} from {source_path.name}. "
            "Use only visible content from this page to answer."
        )
        if CFG.DEBUG_LLM_PROMPTS:
            log_prompt_debug(
                CFG.DOC_FOCUS_DEBUG_PATH,
                [
                    {"role": "system", "content": _load_document_vision_prompt()},
                    {"role": "user", "content": f"{messages[1]['content']}\n\n[ATTACHMENT]\n{attachment_text}"},
                ],
                "DOCUMENT_VISION_FOCUS",
            )
        raw = generate_with_image_attachment(
            llm_client,
            messages=messages,
            image_path=image_path,
            attachment_text=attachment_text,
            temperature=0.0,
            max_tokens=250,
            cancel_token=cancel_token,
        )
        parsed = parse_json_response(raw)
        found = bool(parsed.get("found") or parsed.get("answer_found"))
        answer = str(parsed.get("answer") or parsed.get("relevant_info") or "").strip()
        label = str(parsed.get("label") or parsed.get("matched_label") or "").strip()
        parsed_refs = parsed.get("references")
        if isinstance(parsed_refs, list):
            refs = [str(item).strip() for item in parsed_refs if str(item).strip()] or refs
        if found and answer:
            if not _visual_label_matches_query(query, label):
                return None
            if label and label.lower() not in answer.lower():
                answer = f"{label}: {answer}"
            return answer, refs[:6]
        return None
    finally:
        try:
            image_path.unlink(missing_ok=True)
        except Exception:
            pass


def _extract_visual_document_focus(
    *,
    llm_client,
    query: str,
    document_hits: List[Dict[str, Any]],
    cancel_token=None,
) -> DocumentFocusResult | None:
    if not _supports_visual_fallback():
        return None
    candidates = _build_visual_candidates(document_hits)
    if not candidates:
        return None
    attempted_pages = [f"Page {item['page_number']}" for item in candidates]
    for candidate in candidates:
        result = _extract_visual_answer_for_page(
            llm_client=llm_client,
            query=query,
            candidate=candidate,
            cancel_token=cancel_token,
        )
        if result is None:
            continue
        answer, refs = result
        return DocumentFocusResult(
            relevant_info=answer,
            references=refs,
            source_names=[str(candidate.get("name") or "")],
            used_visual_fallback=True,
            visual_pages=attempted_pages,
        )
    return DocumentFocusResult(
        relevant_info="",
        references=[],
        source_names=[],
        used_visual_fallback=False,
        visual_pages=attempted_pages,
    )


def extract_document_focus(
    *,
    llm_client,
    query: str,
    document_hits: List[Dict[str, Any]],
    cancel_token=None,
) -> DocumentFocusResult:
    hits = [dict(hit) for hit in document_hits if isinstance(hit, dict)]
    source_names = _collect_source_names(hits)
    references = _collect_reference_labels(query, hits)
    if not hits:
        return DocumentFocusResult(source_names=source_names, references=references)

    messages = build_document_focus_messages(query, hits)
    raw = llm_client.generate(
        messages,
        temperature=0.1,
        max_tokens=350,
        cancel_token=cancel_token,
    )
    parsed = parse_json_response(raw)

    relevant_info = str(
        parsed.get("relevant_info")
        or parsed.get("answer_context")
        or parsed.get("answer")
        or ""
    ).strip()

    parsed_refs = parsed.get("references")
    if isinstance(parsed_refs, list):
        for item in parsed_refs:
            label = str(item or "").strip()
            if label and label not in references:
                references.append(label)

    result = DocumentFocusResult(
        relevant_info=relevant_info,
        references=references[:6],
        source_names=source_names,
    )
    if _query_needs_visual_fallback(query, result.relevant_info):
        visual_result = _extract_visual_document_focus(
            llm_client=llm_client,
            query=query,
            document_hits=hits,
            cancel_token=cancel_token,
        )
        if visual_result is not None:
            if visual_result.relevant_info:
                merged_sources = result.source_names or visual_result.source_names
                return DocumentFocusResult(
                    relevant_info=visual_result.relevant_info,
                    references=visual_result.references[:6] or result.references[:6],
                    source_names=merged_sources,
                    used_visual_fallback=True,
                    visual_pages=visual_result.visual_pages,
                )
            return DocumentFocusResult(
                relevant_info="",
                references=result.references[:6],
                source_names=result.source_names,
                used_visual_fallback=False,
                visual_pages=visual_result.visual_pages,
            )
    return result
