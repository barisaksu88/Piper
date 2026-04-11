from __future__ import annotations

import html
import json
import os
import re
import shutil
import threading
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

import requests

from config import CFG
from core.runtime_control import CancellationToken

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - optional dependency
    PlaywrightError = RuntimeError
    PlaywrightTimeoutError = RuntimeError
    sync_playwright = None


_ATTR_SELECTOR_RE = re.compile(
    r"""^\[(?P<name>data-testid|name)=['"](?P<value>[^'"]+)['"]\]$""",
    re.IGNORECASE,
)
_HTTP_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
_FILE_SCHEME_RE = re.compile(r"^file://", re.IGNORECASE)
_TEXT_SELECTOR_RE = re.compile(r"^text=(?P<value>.+)$", re.IGNORECASE)
_HAS_TEXT_SELECTOR_RE = re.compile(
    r"""^(?:(?P<tag>[a-z][\w\-]*)\s*)?:has-text\((?P<quote>['"])(?P<value>.+?)(?P=quote)\)$""",
    re.IGNORECASE,
)
_HEADING_TAGS = {"h1", "h2", "h3", "h4"}
_TOPIC_CANDIDATE_TAGS = {"h1", "h2", "h3", "h4", "p", "li", "dt", "dd", "pre", "blockquote"}
_TOPIC_STOPWORDS = {
    "a",
    "an",
    "and",
    "any",
    "about",
    "details",
    "detail",
    "for",
    "from",
    "get",
    "give",
    "info",
    "information",
    "me",
    "more",
    "of",
    "on",
    "please",
    "read",
    "retrieve",
    "section",
    "show",
    "tell",
    "the",
    "there",
    "this",
    "to",
    "what",
}
_GENERIC_TOPIC_TOKENS = {"general", "overview", "summary", "background", "basics", "introduction"}
_BROWSER_CHROME_RE = re.compile(
    r"(?i)\b(index|modules|previous|next|navigation|contents|skip to|breadcrumb|sidebar|menu)\b"
)
_LOCAL_HTML_PAGE_SUFFIXES = {".html", ".htm"}
_DOWNLOAD_HINT_STOPWORDS = {
    "a",
    "an",
    "artifact",
    "button",
    "download",
    "file",
    "into",
    "link",
    "please",
    "save",
    "the",
    "this",
    "to",
    "version",
}
_DOWNLOAD_TOKEN_ALIASES = {
    "archive": {".zip", ".tar", ".tar.gz", ".tgz", ".gz", ".bz2", ".xz", "archive"},
    "checksum": {".sha1", ".sha256", ".sha512", "checksum", "md5", "sha1", "sha256", "sha512", "sig", "signature"},
    "html": {".htm", ".html", "htm", "html"},
    "installer": {".deb", ".dmg", ".exe", ".msi", ".pkg", ".rpm", "install", "installer", "setup"},
    "pdf": {".pdf", "pdf"},
    "source": {".tar", ".tar.gz", ".tgz", ".zip", "source", "src"},
    "text": {".md", ".rst", ".txt", "plain", "readme", "text", "txt"},
}
_DOWNLOAD_AUXILIARY_PATH_TOKENS = {
    "bib",
    "bibtex",
    "cite",
    "citation",
    "citations",
    "doi",
    "errata",
    "history",
    "ref",
    "reference",
    "references",
    "refs",
    "ris",
    "xml",
}
_DOWNLOAD_IDENTITY_STOPWORDS = _DOWNLOAD_HINT_STOPWORDS | {
    "default",
    "doc",
    "docs",
    "document",
    "download",
    "downloads",
    "file",
    "files",
    "home",
    "index",
    "info",
    "page",
    "pages",
}


class BrowserOpError(RuntimeError):
    pass


class BrowserScopeError(BrowserOpError):
    pass


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    text_parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(part.strip() for part in self.text_parts if str(part).strip()).strip()


class _SimplePageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[_Node] = []
        self._stack: list[int] = []
        self._capture_title = False
        self._title_parts: list[str] = []

    @property
    def title(self) -> str:
        return " ".join(part.strip() for part in self._title_parts if str(part).strip()).strip()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        node = _Node(tag=str(tag or "").lower(), attrs={str(k): str(v or "") for k, v in attrs})
        self.nodes.append(node)
        self._stack.append(len(self.nodes) - 1)
        if node.tag == "title":
            self._capture_title = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        node = _Node(tag=str(tag or "").lower(), attrs={str(k): str(v or "") for k, v in attrs})
        self.nodes.append(node)

    def handle_endtag(self, tag: str) -> None:
        lowered = str(tag or "").lower()
        if lowered == "title":
            self._capture_title = False
        if self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        text = str(data or "")
        if not text.strip():
            return
        if self._capture_title:
            self._title_parts.append(text)
        for idx in self._stack:
            if 0 <= idx < len(self.nodes):
                self.nodes[idx].text_parts.append(text)


@dataclass
class _BrowserSessionState:
    backend: str = "none"
    current_url: str = ""
    current_title: str = ""
    current_html: str = ""
    nodes: list[_Node] = field(default_factory=list)
    page_text: str = ""
    allowed_domains: list[str] = field(default_factory=list)
    field_values: dict[str, str] = field(default_factory=dict)


class ComputerUseEngine:
    def __init__(self, *, data_dir: Path, workspace: Path) -> None:
        self.data_dir = Path(data_dir)
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.session_dir = self.data_dir / "computer_use"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._session = _BrowserSessionState()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._playwright_owner_thread: threading.Thread | None = None
        self._playwright_lock = threading.RLock()

    def shutdown(self) -> None:
        with self._playwright_lock:
            self._reset_playwright_session()
            self._session = _BrowserSessionState()

    def suspend(self) -> None:
        with self._playwright_lock:
            self._reset_playwright_session()

    @staticmethod
    def _raise_if_cancelled(cancel_token: CancellationToken | None) -> None:
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()

    @staticmethod
    def _result(
        *,
        status: str,
        action: str,
        summary: str,
        backend: str = "",
        **extra: Any,
    ) -> dict[str, Any]:
        payload = {
            "tool": "BROWSER_OP",
            "status": status,
            "action": action,
            "summary": summary,
            "backend": backend,
        }
        payload.update(extra)
        return payload

    @staticmethod
    def _normalize_domains(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            token = str(item or "").strip().lower()
            if not token:
                continue
            if token.startswith("www."):
                token = token[4:]
            if token not in seen:
                seen.add(token)
                cleaned.append(token)
        return cleaned

    @staticmethod
    def _host_matches_allowed(host: str, allowed_domains: list[str]) -> bool:
        host_l = str(host or "").strip().lower()
        if host_l.startswith("www."):
            host_l = host_l[4:]
        return any(host_l == domain or host_l.endswith("." + domain) for domain in allowed_domains)

    def _enforce_scope(self, url: str, allowed_domains: list[str]) -> None:
        if not bool(getattr(CFG, "COMPUTER_USE_ENABLED", True)):
            raise BrowserScopeError("Browser action blocked: computer use is disabled by configuration.")
        if not _HTTP_SCHEME_RE.match(url):
            return
        if not bool(getattr(CFG, "COMPUTER_USE_HTTP_ENABLED", True)):
            raise BrowserScopeError(
                "Browser action blocked: live HTTP/HTTPS browser automation is disabled by configuration."
            )
        domains = self._normalize_domains(allowed_domains)
        if not domains:
            raise BrowserScopeError("HTTP/HTTPS browser actions require allowed_domains for scope enforcement.")
        host = str(urllib.parse.urlparse(url).hostname or "").strip().lower()
        if not host:
            raise BrowserScopeError(f"Could not determine browser scope host for URL: {url}")
        configured_domains = self._normalize_domains(getattr(CFG, "COMPUTER_USE_ALLOWED_HTTP_DOMAINS", []))
        if configured_domains and not self._host_matches_allowed(host, configured_domains):
            raise BrowserScopeError(
                "Browser action blocked: host "
                f"'{host}' is outside the live-site browser pilot allowlist ({', '.join(configured_domains)})."
            )
        if not self._host_matches_allowed(host, domains):
            raise BrowserScopeError(
                f"Browser action blocked: host '{host}' is outside the allowed browser scope ({', '.join(domains)})."
            )

    @staticmethod
    def _resolve_file_url(url: str) -> Path:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme and parsed.scheme.lower() != "file":
            raise BrowserOpError(f"Only file:// URLs can be resolved locally, not: {url}")
        raw_path = urllib.parse.unquote(parsed.path or "")
        if parsed.netloc:
            raw_path = f"//{parsed.netloc}{raw_path}"
        return Path(raw_path)

    @staticmethod
    def _strip_text_from_html(html_text: str) -> str:
        text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html_text)
        text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return html.unescape(text).strip()

    @staticmethod
    def _compact_text(value: str, *, limit: int = 120) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

    @staticmethod
    def _normalize_download_hint_tokens(value: str) -> list[str]:
        tokens = re.findall(r"[a-z0-9]+", str(value or "").lower())
        normalized: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if not token or token in _DOWNLOAD_HINT_STOPWORDS:
                continue
            if token.endswith("ies") and len(token) > 4:
                token = token[:-3] + "y"
            elif token.endswith("s") and len(token) > 4 and token not in {"status"}:
                token = token[:-1]
            if token in _DOWNLOAD_HINT_STOPWORDS or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        return normalized

    @staticmethod
    def _download_url_stem(value: str) -> str:
        path = str(urllib.parse.urlparse(str(value or "").strip()).path or "").strip()
        if not path:
            return ""
        name = Path(path).name or Path(path).stem
        stem = Path(name).stem if Path(name).suffix else name
        normalized = re.sub(r"[^a-z0-9]+", "", stem.lower())
        if len(normalized) < 3 or normalized in _DOWNLOAD_IDENTITY_STOPWORDS:
            return ""
        return normalized

    @classmethod
    def _download_identity_tokens(cls, *values: str) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            for token in cls._normalize_download_hint_tokens(value):
                if len(token) < 3 or token in _DOWNLOAD_IDENTITY_STOPWORDS or token in seen:
                    continue
                seen.add(token)
                normalized.append(token)
        return normalized

    @staticmethod
    def _download_candidate_haystack(candidate: dict[str, Any]) -> str:
        parts = [
            str(candidate.get("text") or ""),
            str(candidate.get("href") or ""),
            str(candidate.get("download") or ""),
            str(candidate.get("selector") or ""),
            str(candidate.get("id") or ""),
            str(candidate.get("data_testid") or ""),
            str(candidate.get("name") or ""),
        ]
        return " ".join(part for part in parts if part).lower()

    @classmethod
    def _score_download_candidate(
        cls,
        candidate: dict[str, Any],
        hint: str,
        *,
        current_url: str = "",
        current_title: str = "",
    ) -> int:
        hint_l = str(hint or "").strip().lower()
        if not hint_l:
            return -10**9
        text = str(candidate.get("text") or "").strip().lower()
        href = str(candidate.get("href") or "").strip().lower()
        download_attr = str(candidate.get("download") or "").strip().lower()
        haystack = cls._download_candidate_haystack(candidate)
        tokens = cls._normalize_download_hint_tokens(hint_l)
        href_path_tokens = set(re.findall(r"[a-z0-9]+", urllib.parse.urlparse(href).path))
        current_stem = cls._download_url_stem(current_url)
        candidate_stem = cls._download_url_stem(href)
        identity_tokens = cls._download_identity_tokens(current_stem, current_title)
        matched = 0
        score = 0

        if hint_l in haystack:
            score += 80

        for token in tokens:
            aliases = set(_DOWNLOAD_TOKEN_ALIASES.get(token, set()))
            aliases.add(token)
            token_score = 0
            if token in text:
                token_score = max(token_score, 28)
            if any(alias and alias in haystack for alias in aliases):
                token_score = max(token_score, 22)
            if token == "text" and href.endswith(".txt"):
                token_score = max(token_score, 44)
            elif token == "pdf" and href.endswith(".pdf"):
                token_score = max(token_score, 44)
            elif token == "html" and href.endswith((".html", ".htm")):
                token_score = max(token_score, 28)
            elif token == "checksum" and any(
                href.endswith(suffix) for suffix in (".sha256", ".sha512", ".sha1", ".md5", ".sig")
            ):
                token_score = max(token_score, 44)
            elif token == "archive" and any(
                href.endswith(suffix) for suffix in (".zip", ".tar", ".tar.gz", ".tgz", ".gz", ".bz2", ".xz")
            ):
                token_score = max(token_score, 40)
            elif token == "installer" and any(
                href.endswith(suffix) for suffix in (".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm")
            ):
                token_score = max(token_score, 40)
            if token_score > 0:
                matched += 1
            score += token_score

        if tokens and matched:
            score += matched * 8
        if tokens and matched == len(tokens):
            score += 20
        if text in {"text", "pdf", "html"} and text in tokens:
            score += 24
        if current_stem and candidate_stem:
            if candidate_stem == current_stem:
                score += 72
            elif current_stem in candidate_stem or candidate_stem in current_stem:
                score += 34
        identity_hits = 0
        for token in identity_tokens:
            if token and token in haystack:
                identity_hits += 1
        if identity_hits:
            score += min(identity_hits, 3) * 12
        if href and not cls._looks_like_html_page_href(href, download_attr):
            score += 16
        if download_attr:
            score += 14
        if href_path_tokens & _DOWNLOAD_AUXILIARY_PATH_TOKENS:
            score -= 42
            if text in {"txt", "pdf", "html", "xml"}:
                score -= 10
        if cls._looks_like_html_page_href(href, download_attr):
            score -= 80
        elif href:
            suffix = Path(urllib.parse.urlparse(href).path).suffix.lower()
            if suffix and suffix not in _LOCAL_HTML_PAGE_SUFFIXES:
                score += 10
        if not text and not href:
            score -= 12
        return score

    @classmethod
    def _best_download_candidate(
        cls,
        candidates: list[dict[str, Any]],
        *,
        hint: str,
        current_url: str = "",
        current_title: str = "",
    ) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_score = -10**9
        for candidate in candidates:
            score = cls._score_download_candidate(
                candidate,
                hint,
                current_url=current_url,
                current_title=current_title,
            )
            if score > best_score:
                best = dict(candidate)
                best_score = score
        if best is None or best_score < 28:
            return None
        best["match_score"] = int(best_score)
        return best

    @staticmethod
    def _selector_key(selector: str, node: _Node) -> str:
        selector = str(selector or "").strip()
        if selector:
            return selector
        for attr_name in ("id", "name", "data-testid"):
            value = str(node.attrs.get(attr_name) or "").strip()
            if value:
                return value
        return node.tag

    @staticmethod
    def _node_selector_hint(node: _Node) -> str:
        node_id = str(node.attrs.get("id") or "").strip()
        if node_id:
            return f"#{node_id}"
        data_testid = str(node.attrs.get("data-testid") or "").strip()
        if data_testid:
            return f"[data-testid='{data_testid}']"
        name = str(node.attrs.get("name") or "").strip()
        if name:
            return f"[name='{name}']"
        return node.tag

    @staticmethod
    def _escape_css_attr_value(value: str) -> str:
        return str(value or "").replace("\\", "\\\\").replace("'", "\\'")

    @classmethod
    def _selector_from_candidate_fields(
        cls,
        *,
        tag: str,
        id_value: str = "",
        data_testid: str = "",
        name: str = "",
        href: str = "",
    ) -> str:
        if id_value:
            if re.fullmatch(r"[-_A-Za-z][-_A-Za-z0-9]*", id_value):
                return f"#{id_value}"
            return f"[id='{cls._escape_css_attr_value(id_value)}']"
        if data_testid:
            return f"[data-testid='{cls._escape_css_attr_value(data_testid)}']"
        if name:
            return f"[name='{cls._escape_css_attr_value(name)}']"
        if tag == "a" and href:
            return f"a[href='{cls._escape_css_attr_value(href)}']"
        return tag

    @staticmethod
    def _node_match_haystack(node: _Node) -> str:
        return " ".join(
            [
                node.text,
                str(node.attrs.get("id") or ""),
                str(node.attrs.get("data-testid") or ""),
                str(node.attrs.get("name") or ""),
                str(node.attrs.get("href") or node.attrs.get("data-href") or ""),
            ]
        ).lower()

    def _normalize_selector_for_current_page(self, selector: str) -> str:
        raw = str(selector or "").strip()
        if not raw:
            return ""
        if raw.startswith(("#", "[", "text=")) or ":" in raw or " " in raw:
            return raw

        inventory: list[dict[str, str]] = []
        if self._session.backend == "playwright":
            inventory = self._capture_playwright_element_inventory()
        elif self._session.nodes:
            inventory = self._build_local_element_inventory()
        for item in inventory:
            if raw == str(item.get("selector") or "").strip():
                return raw
            if raw == str(item.get("id") or "").strip():
                return f"#{raw}"
            if raw == str(item.get("data_testid") or "").strip():
                return f"[data-testid='{raw}']"
            if raw == str(item.get("name") or "").strip():
                return f"[name='{raw}']"
        return raw

    def _build_local_element_inventory(self) -> list[dict[str, str]]:
        inventory: list[dict[str, str]] = []
        for node in self._session.nodes:
            tag = str(node.tag or "").strip().lower()
            if not tag:
                continue
            text_value = self._compact_text(node.text)
            node_id = str(node.attrs.get("id") or "").strip()
            data_testid = str(node.attrs.get("data-testid") or "").strip()
            name = str(node.attrs.get("name") or "").strip()
            href = str(node.attrs.get("href") or node.attrs.get("data-href") or "").strip()
            input_type = str(node.attrs.get("type") or "").strip()
            if not any((node_id, data_testid, name, href, text_value)):
                continue
            if not any((node_id, data_testid, name, href)) and tag not in {
                "a",
                "button",
                "h1",
                "h2",
                "h3",
                "input",
                "textarea",
                "select",
                "label",
                "option",
            }:
                continue
            if tag in {"html", "head", "title", "meta", "link", "script", "style"}:
                continue
            item: dict[str, str] = {
                "tag": tag,
                "selector": self._node_selector_hint(node),
            }
            if node_id:
                item["id"] = node_id
            if data_testid:
                item["data_testid"] = data_testid
            if name:
                item["name"] = name
            if href:
                item["href"] = href
            if input_type:
                item["type"] = input_type
            if text_value:
                item["text"] = text_value
            inventory.append(item)
            if len(inventory) >= 12:
                break
        return inventory

    def _capture_playwright_interaction_candidates(self, *, limit: int = 60) -> list[dict[str, str]]:
        page = self._ensure_playwright_page()
        try:
            raw_items = page.evaluate(
                """(limit) => {
                    const root =
                      document.querySelector("main, article, [role='main'], .body, .document, .main-content") ||
                      document.body ||
                      document.documentElement;
                    const isVisible = (el) => {
                      const style = window.getComputedStyle(el);
                      if (!style) return false;
                      if (style.display === "none" || style.visibility === "hidden") return false;
                      if (el.hasAttribute("hidden") || el.getAttribute("aria-hidden") === "true") return false;
                      const rects = el.getClientRects();
                      return Boolean(rects && rects.length);
                    };
                    const nodes = Array.from(
                      root.querySelectorAll("a, button, input, textarea, select, [data-testid], [id], [name], h1, h2, h3")
                    )
                      .filter((el) => {
                        const tag = String(el.tagName || "").toLowerCase();
                        if (!tag || ["html", "head", "title", "meta", "link", "script", "style"].includes(tag)) {
                          return false;
                        }
                        if (["a", "button", "input", "textarea", "select", "h1", "h2", "h3"].includes(tag) && !isVisible(el)) {
                          return false;
                        }
                        const id = String(el.getAttribute("id") || "");
                        const dataTestid = String(el.getAttribute("data-testid") || "");
                        const name = String(el.getAttribute("name") || "");
                        const href = String(el.getAttribute("href") || "");
                        const text = String((el.innerText || el.textContent || "")).replace(/\\s+/g, " ").trim();
                        return Boolean(id || dataTestid || name || href || text);
                      })
                      .slice(0, limit);
                    return nodes.map((el) => {
                      const tag = String(el.tagName || "").toLowerCase();
                      const id = String(el.getAttribute("id") || "");
                      const dataTestid = String(el.getAttribute("data-testid") || "");
                      const name = String(el.getAttribute("name") || "");
                      const href = String(el.getAttribute("href") || "");
                      const type = String(el.getAttribute("type") || "");
                      const download = String(el.getAttribute("download") || "");
                      const text = String((el.innerText || el.textContent || "")).replace(/\\s+/g, " ").trim();
                      return { tag, id, data_testid: dataTestid, name, href, type, download, text };
                    });
                }""",
                limit,
            )
        except Exception:  # pragma: no cover - best effort only
            return []
        candidates: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_items or []:
            if not isinstance(item, dict):
                continue
            tag = str(item.get("tag") or "").strip().lower()
            if not tag:
                continue
            id_value = str(item.get("id") or "").strip()
            data_testid = str(item.get("data_testid") or "").strip()
            name_value = str(item.get("name") or "").strip()
            href_value = str(item.get("href") or "").strip()
            selector = self._selector_from_candidate_fields(
                tag=tag,
                id_value=id_value,
                data_testid=data_testid,
                name=name_value,
                href=href_value,
            )
            key = f"{tag}|{selector}"
            if key in seen:
                continue
            seen.add(key)
            entry: dict[str, str] = {"tag": tag, "selector": selector}
            for field_name, value in (
                ("id", id_value),
                ("data_testid", data_testid),
                ("name", name_value),
                ("href", href_value),
                ("type", str(item.get("type") or "").strip()),
                ("download", str(item.get("download") or "").strip()),
            ):
                if value:
                    entry[field_name] = value
            text_value = self._compact_text(str(item.get("text") or ""), limit=180)
            if text_value:
                entry["text"] = text_value
            candidates.append(entry)
        return candidates

    def _capture_playwright_element_inventory(self) -> list[dict[str, str]]:
        inventory: list[dict[str, str]] = []
        for item in self._capture_playwright_interaction_candidates(limit=60):
            tag = str(item.get("tag") or "").strip().lower()
            selector = str(item.get("selector") or "").strip()
            id_value = str(item.get("id") or "").strip()
            data_testid = str(item.get("data_testid") or "").strip()
            name_value = str(item.get("name") or "").strip()
            href_value = str(item.get("href") or "").strip()
            if not any((id_value, data_testid, name_value, href_value)) and tag not in {
                "a",
                "button",
                "h1",
                "h2",
                "h3",
                "input",
                "textarea",
                "select",
                "label",
                "option",
            }:
                continue
            entry: dict[str, str] = {"tag": tag, "selector": selector}
            for field_name in ("id", "data_testid", "name", "href", "type"):
                value = str(item.get(field_name) or "").strip()
                if value:
                    entry[field_name] = value
            text_value = self._compact_text(str(item.get("text") or ""))
            if text_value:
                entry["text"] = text_value
            inventory.append(entry)
            if len(inventory) >= 12:
                break
        return inventory

    def _capture_playwright_download_candidates(self) -> list[dict[str, str]]:
        candidates: list[dict[str, str]] = []
        for item in self._capture_playwright_interaction_candidates(limit=80):
            tag = str(item.get("tag") or "").strip().lower()
            href = str(item.get("href") or "").strip()
            download_attr = str(item.get("download") or "").strip()
            text_value = str(item.get("text") or "").strip()
            if tag not in {"a", "button"} and not href and not download_attr:
                continue
            if not any((href, download_attr, text_value)):
                continue
            candidates.append(dict(item))
        return candidates

    @staticmethod
    def _parse_page(html_text: str) -> tuple[str, list[_Node], str]:
        parser = _SimplePageParser()
        parser.feed(html_text)
        parser.close()
        return parser.title, parser.nodes, ComputerUseEngine._strip_text_from_html(html_text)

    @staticmethod
    def _normalize_topic_tokens(value: str) -> list[str]:
        tokens = re.findall(r"[a-z0-9]+", str(value or "").lower())
        normalized: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if not token or token in _TOPIC_STOPWORDS:
                continue
            if token.endswith("ies") and len(token) > 4:
                token = token[:-3] + "y"
            elif token.endswith("s") and len(token) > 4:
                token = token[:-1]
            if token in _TOPIC_STOPWORDS or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        return normalized

    @staticmethod
    def _is_generic_topic(topic: str, tokens: list[str]) -> bool:
        topic_l = str(topic or "").strip().lower()
        if not topic_l:
            return False
        if topic_l in {"general info", "general information", "overview", "summary"}:
            return True
        return any(token in _GENERIC_TOPIC_TOKENS for token in tokens)

    @staticmethod
    def _build_topic_candidates(
        blocks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()
        current_heading: dict[str, Any] | None = None
        section_blocks: list[dict[str, Any]] = []
        section_start = 0

        def add_candidate(
            *,
            selector: str,
            text: str,
            heading: str = "",
            heading_selector: str = "",
            order: int = 0,
            kind: str = "block",
        ) -> None:
            cleaned_text = re.sub(r"\s+", " ", str(text or "")).strip()
            cleaned_heading = re.sub(r"\s+", " ", str(heading or "")).strip()
            if not cleaned_text:
                return
            key = (selector.strip(), cleaned_text)
            if key in seen_keys:
                return
            seen_keys.add(key)
            candidates.append(
                {
                    "selector": str(selector or "").strip(),
                    "text": cleaned_text,
                    "heading": cleaned_heading,
                    "heading_selector": str(heading_selector or "").strip(),
                    "order": int(order),
                    "kind": kind,
                }
            )

        def flush_section() -> None:
            nonlocal current_heading, section_blocks, section_start
            if current_heading is None:
                section_blocks = []
                return
            section_text_parts = [str(current_heading.get("text") or "").strip()]
            section_text_parts.extend(str(block.get("text") or "").strip() for block in section_blocks if str(block.get("text") or "").strip())
            combined_text = "\n".join(part for part in section_text_parts if part)
            if combined_text.strip():
                add_candidate(
                    selector=str(current_heading.get("selector") or "").strip(),
                    text=combined_text,
                    heading=str(current_heading.get("text") or "").strip(),
                    heading_selector=str(current_heading.get("selector") or "").strip(),
                    order=section_start,
                    kind="section",
                )
            current_heading = None
            section_blocks = []

        for index, block in enumerate(blocks):
            tag = str(block.get("tag") or "").strip().lower()
            text = str(block.get("text") or "").strip()
            selector = str(block.get("selector") or "").strip()
            if not text:
                continue
            if tag in _HEADING_TAGS:
                flush_section()
                current_heading = block
                section_start = index
                add_candidate(
                    selector=selector,
                    text=text,
                    heading=text,
                    heading_selector=selector,
                    order=index,
                    kind="heading",
                )
                continue

            add_candidate(
                selector=selector,
                text=text,
                heading=str(current_heading.get("text") or "").strip() if current_heading else "",
                heading_selector=str(current_heading.get("selector") or "").strip() if current_heading else "",
                order=index,
                kind="block",
            )
            if current_heading is not None:
                section_blocks.append(block)
                section_text = " ".join(str(item.get("text") or "").strip() for item in section_blocks)
                if len(section_text) >= 900 or len(section_blocks) >= 4:
                    flush_section()
        flush_section()
        return candidates

    @staticmethod
    def _score_topic_candidate(
        candidate: dict[str, Any],
        *,
        topic: str,
        topic_tokens: list[str],
        generic_topic: bool,
        selector_hint: str = "",
        text_hint: str = "",
        avoid_heading: str = "",
    ) -> int:
        text = str(candidate.get("text") or "").strip()
        heading = str(candidate.get("heading") or "").strip()
        selector = str(candidate.get("selector") or "").strip()
        heading_selector = str(candidate.get("heading_selector") or "").strip()
        kind = str(candidate.get("kind") or "").strip().lower()
        haystack = " ".join(part for part in (heading, text, selector, heading_selector) if part).lower()
        heading_l = heading.lower()
        topic_l = str(topic or "").strip().lower()
        avoid_heading_l = str(avoid_heading or "").strip().lower()
        score = 0

        if generic_topic:
            if topic_l and topic_l in heading_l:
                score += 90
            elif topic_l and topic_l in haystack:
                score += 60
            for token in topic_tokens:
                if token in heading_l:
                    score += 28
                elif token in haystack:
                    score += 12
            if kind == "section":
                score += 40
            elif kind == "block":
                score += 25
            score += max(0, 24 - int(candidate.get("order") or 0) * 3)
            if 60 <= len(text) <= 700:
                score += 18
            elif len(text) >= 35:
                score += 8
            if heading and heading != text:
                score += 8
        else:
            if topic_l and topic_l in heading_l:
                score += 140
            elif topic_l and topic_l in haystack:
                score += 110
            overlap = 0
            heading_overlap = 0
            for token in topic_tokens:
                if token in heading_l:
                    heading_overlap += 1
                    score += 36
                elif token in haystack:
                    overlap += 1
                    score += 16
            if heading_overlap:
                score += 18
            if heading_overlap + overlap >= max(1, min(2, len(topic_tokens))):
                score += 28
            if kind == "section":
                score += 24
            elif kind == "block":
                score += 12
            if 35 <= len(text) <= 900:
                score += 12

        selector_hint_l = str(selector_hint or "").strip().lower()
        if selector_hint_l and selector_hint_l not in {"body", "html"}:
            if selector_hint_l in {selector.lower(), heading_selector.lower()}:
                score += 36
            elif selector_hint_l in haystack:
                score += 14

        text_hint_l = str(text_hint or "").strip().lower()
        if text_hint_l:
            if text_hint_l in haystack:
                score += 24
            else:
                for token in ComputerUseEngine._normalize_topic_tokens(text_hint_l):
                    if token in haystack:
                        score += 8

        chrome_hits = len(_BROWSER_CHROME_RE.findall(text))
        if chrome_hits:
            score -= min(chrome_hits, 4) * 14
        if avoid_heading_l and heading_l and heading_l == avoid_heading_l:
            score -= 220
        if selector.lower() in {"body", "html"}:
            score -= 24
        if len(text) < 30:
            score -= 18
        return score

    def _extract_topic_text_from_blocks(
        self,
        *,
        blocks: list[dict[str, Any]],
        title: str,
        topic: str,
        selector_hint: str = "",
        text_hint: str = "",
        avoid_heading: str = "",
    ) -> dict[str, Any] | None:
        normalized_topic = str(topic or "").strip()
        if not normalized_topic:
            return None
        candidates = self._build_topic_candidates(blocks)
        if not candidates:
            return None
        topic_tokens = self._normalize_topic_tokens(normalized_topic)
        generic_topic = self._is_generic_topic(normalized_topic, topic_tokens)

        best: dict[str, Any] | None = None
        best_score = -10**9
        for candidate in candidates:
            score = self._score_topic_candidate(
                candidate,
                topic=normalized_topic,
                topic_tokens=topic_tokens,
                generic_topic=generic_topic,
                selector_hint=selector_hint,
                text_hint=text_hint,
                avoid_heading=avoid_heading,
            )
            if score > best_score:
                best = candidate
                best_score = score

        if best is None:
            return None

        extracted_text = str(best.get("text") or "").strip()
        if not extracted_text:
            return None
        return {
            "topic": normalized_topic,
            "selector": str(best.get("selector") or selector_hint or "body").strip(),
            "matched_heading": str(best.get("heading") or title or "").strip(),
            "topic_match_score": int(best_score),
            "extraction_strategy": "topic_ranked_extract",
            "extracted_text": extracted_text,
        }

    def _build_local_topic_blocks(self) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for node in self._session.nodes:
            tag = str(node.tag or "").strip().lower()
            if tag not in _TOPIC_CANDIDATE_TAGS:
                continue
            text = re.sub(r"\s+", " ", str(node.text or "")).strip()
            if len(text) < 8:
                continue
            blocks.append(
                {
                    "tag": tag,
                    "selector": self._node_selector_hint(node),
                    "text": text,
                }
            )
        return blocks

    def _capture_playwright_text_blocks(self) -> list[dict[str, Any]]:
        page = self._ensure_playwright_page()
        try:
            raw_items = page.evaluate(
                """() => {
                    const root =
                      document.querySelector("main, article, [role='main'], .body, .document, .main-content") ||
                      document.body;
                    const nodes = Array.from(
                      root.querySelectorAll("h1, h2, h3, h4, p, li, dt, dd, pre, blockquote")
                    ).slice(0, 120);
                    return nodes.map((el) => {
                      const tag = String((el.tagName || "")).toLowerCase();
                      const id = String(el.getAttribute("id") || "");
                      const dataTestid = String(el.getAttribute("data-testid") || "");
                      const name = String(el.getAttribute("name") || "");
                      const text = String((el.innerText || el.textContent || "")).replace(/\\s+/g, " ").trim();
                      let selector = tag;
                      if (id) selector = `#${id}`;
                      else if (dataTestid) selector = `[data-testid='${dataTestid}']`;
                      else if (name) selector = `[name='${name}']`;
                      return { tag, selector, text };
                    });
                }"""
            )
        except Exception:  # pragma: no cover - best effort only
            return []
        blocks: list[dict[str, Any]] = []
        for item in raw_items or []:
            if not isinstance(item, dict):
                continue
            tag = str(item.get("tag") or "").strip().lower()
            text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
            selector = str(item.get("selector") or "").strip()
            if tag not in _TOPIC_CANDIDATE_TAGS or len(text) < 8:
                continue
            blocks.append({"tag": tag, "selector": selector or tag, "text": text})
        return blocks

    def _find_node(self, *, selector: str = "", text: str = "") -> tuple[Optional[_Node], str]:
        selector = self._normalize_selector_for_current_page(selector)
        text = str(text or "").strip()
        nodes = list(self._session.nodes)
        if selector:
            has_text_match = _HAS_TEXT_SELECTOR_RE.match(selector)
            if has_text_match:
                target_tag = str(has_text_match.group("tag") or "").strip().lower()
                needle = str(has_text_match.group("value") or "").strip().lower()
                if needle:
                    for require_tag in ((True, False) if target_tag else (False,)):
                        for node in nodes:
                            if require_tag and node.tag != target_tag:
                                continue
                            haystack = self._node_match_haystack(node)
                            if needle in haystack:
                                return node, "has_text"
            simple_tag = selector.lower()
            if re.fullmatch(r"[a-z][a-z0-9_-]*", simple_tag):
                for node in nodes:
                    if node.tag == simple_tag:
                        return node, "tag"
            if selector.startswith("#"):
                target = selector[1:]
                for node in nodes:
                    if str(node.attrs.get("id") or "") == target:
                        return node, "id"
            attr_match = _ATTR_SELECTOR_RE.match(selector)
            if attr_match:
                attr_name = str(attr_match.group("name") or "").strip()
                attr_value = str(attr_match.group("value") or "").strip()
                for node in nodes:
                    if str(node.attrs.get(attr_name) or "") == attr_value:
                        return node, attr_name
            text_selector = _TEXT_SELECTOR_RE.match(selector)
            if text_selector:
                needle = str(text_selector.group("value") or "").strip().lower()
                for node in nodes:
                    if needle and needle in self._node_match_haystack(node):
                        return node, "text"
            for node in nodes:
                if selector and selector == node.text:
                    return node, "text"
        if text:
            needle = text.lower()
            for node in nodes:
                if needle in self._node_match_haystack(node):
                    return node, "text"
        return None, ""

    @staticmethod
    def _is_local_downloadable_href(target: str, node: _Node) -> bool:
        href = str(target or "").strip()
        if not href:
            return False
        if str(node.attrs.get("download") or "").strip():
            return True
        parsed = urllib.parse.urlparse(href)
        suffix = Path(str(parsed.path or "").strip()).suffix.lower()
        if suffix in _LOCAL_HTML_PAGE_SUFFIXES:
            return False
        return True

    @staticmethod
    def _looks_like_html_page_href(target: str, download_attr: str = "") -> bool:
        href = str(target or "").strip()
        if not href or str(download_attr or "").strip():
            return False
        suffix = Path(urllib.parse.urlparse(href).path).suffix.lower()
        return suffix in _LOCAL_HTML_PAGE_SUFFIXES

    def _find_local_download_node(self, *, selector: str = "", text: str = "") -> tuple[Optional[_Node], str]:
        selector = str(selector or "").strip()
        text = str(text or "").strip()
        if selector:
            node, strategy = self._find_node(selector=selector, text="")
            return node, strategy
        if not text:
            return None, ""

        best: tuple[int, _Node] | None = None
        for node in self._session.nodes:
            target = str(node.attrs.get("href") or node.attrs.get("data-href") or "").strip()
            if not self._is_local_downloadable_href(target, node):
                continue
            score = self._score_download_candidate(
                {
                    "tag": str(node.tag or "").strip().lower(),
                    "selector": self._node_selector_hint(node),
                    "text": str(node.text or "").strip(),
                    "href": target,
                    "download": str(node.attrs.get("download") or "").strip(),
                    "id": str(node.attrs.get("id") or "").strip(),
                    "data_testid": str(node.attrs.get("data-testid") or "").strip(),
                    "name": str(node.attrs.get("name") or "").strip(),
                },
                text,
                current_url=self._session.current_url,
                current_title=self._session.current_title,
            )
            if best is None or score > best[0]:
                best = (score, node)
        if best is not None and best[0] >= 28:
            return best[1], "download_text"
        return None, ""

    def _load_local_page(self, url: str) -> None:
        path = self._resolve_file_url(url)
        if not path.exists() or not path.is_file():
            raise BrowserOpError(f"Local browser page not found: {path}")
        html_text = path.read_text(encoding="utf-8")
        title, nodes, page_text = self._parse_page(html_text)
        self._session.backend = "local_fixture"
        self._session.current_url = path.resolve().as_uri()
        self._session.current_title = title
        self._session.current_html = html_text
        self._session.nodes = nodes
        self._session.page_text = page_text

    def _reset_playwright_session(self) -> None:
        page = self._page
        context = self._context
        browser = self._browser
        playwright = self._playwright

        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._playwright_owner_thread = None

        for handle in (page, context, browser):
            if handle is None:
                continue
            try:
                handle.close()
            except Exception:
                pass

        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    def _ensure_playwright_page(self, *, restore_current_url: bool = True):
        if sync_playwright is None:
            raise BrowserOpError(
                "Playwright is not installed in this environment yet. Local file:// fixture pages still work, "
                "but live browser navigation requires the Playwright package and browser binaries."
            )
        current_thread = threading.current_thread()
        with self._playwright_lock:
            rehydrate_url = ""
            if (
                self._playwright_owner_thread is not None
                and self._playwright_owner_thread is not current_thread
            ):
                if (
                    restore_current_url
                    and self._session.backend == "playwright"
                    and _HTTP_SCHEME_RE.match(self._session.current_url)
                ):
                    rehydrate_url = self._session.current_url
                self._reset_playwright_session()
            if self._page is not None:
                return self._page
            try:
                self._prepare_playwright_linux_lib_path()
                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch(headless=True)
                self._context = self._browser.new_context(accept_downloads=True)
                self._page = self._context.new_page()
                self._playwright_owner_thread = current_thread
                if rehydrate_url:
                    if self._session.allowed_domains:
                        self._enforce_scope(rehydrate_url, self._session.allowed_domains)
                    self._page.goto(rehydrate_url, wait_until="domcontentloaded", timeout=15000)
                return self._page
            except PlaywrightError as exc:  # pragma: no cover - depends on local install
                self._reset_playwright_session()
                raise BrowserOpError(f"Could not start the browser automation backend: {exc}") from exc

    @staticmethod
    def _prepare_playwright_linux_lib_path() -> None:
        if os.name != "posix":
            return
        candidate_dirs: list[Path] = []
        env_dir = str(os.environ.get("PIPER_PLAYWRIGHT_LD_LIBRARY_PATH") or "").strip()
        if env_dir:
            candidate_dirs.append(Path(env_dir))
        repo_root = Path(__file__).resolve().parents[2]
        candidate_dirs.append(repo_root / ".venv-wsl" / "playwright-libs" / "usr" / "lib" / "x86_64-linux-gnu")
        candidate_dirs.append(repo_root / ".venv-wsl" / "playwright-libs" / "usr" / "lib")

        chosen: str = ""
        for candidate in candidate_dirs:
            if not candidate.exists() or not candidate.is_dir():
                continue
            if (candidate / "libnspr4.so").exists() and (candidate / "libnss3.so").exists():
                chosen = str(candidate.resolve())
                break
        if not chosen:
            return

        existing = [item for item in str(os.environ.get("LD_LIBRARY_PATH") or "").split(":") if item]
        if chosen not in existing:
            os.environ["LD_LIBRARY_PATH"] = ":".join([chosen, *existing]) if existing else chosen

    def _selector_for_playwright(self, payload: dict[str, Any]) -> str:
        selector = self._normalize_selector_for_current_page(str(payload.get("selector") or "").strip())
        text = str(payload.get("text") or "").strip()
        if selector:
            return selector
        if text:
            return f"text={text}"
        raise BrowserOpError("BROWSER_OP requires 'selector' or 'text' for this action.")

    def _download_selector_for_playwright(self, payload: dict[str, Any]) -> str:
        selector = self._normalize_selector_for_current_page(str(payload.get("selector") or "").strip())
        text = str(payload.get("text") or "").strip()
        if selector:
            return selector
        if text:
            candidate = self._best_download_candidate(
                self._capture_playwright_download_candidates(),
                hint=text,
                current_url=self._session.current_url,
                current_title=self._session.current_title,
            )
            if candidate is not None:
                return str(candidate.get("selector") or "").strip()
            quoted = json.dumps(text, ensure_ascii=False)
            return f"a:has-text({quoted}), button:has-text({quoted}), [download]:has-text({quoted})"
        raise BrowserOpError("BROWSER_OP download requires 'selector' or 'text' to target an artifact.")

    @staticmethod
    def _filename_from_download_response(response: requests.Response, fallback_url: str) -> str:
        content_disposition = str(response.headers.get("content-disposition") or "").strip()
        filename_star = re.search(r"""filename\*=UTF-8''(?P<value>[^;]+)""", content_disposition, re.IGNORECASE)
        if filename_star:
            value = urllib.parse.unquote(str(filename_star.group("value") or "").strip())
            if value:
                return value
        filename_match = re.search(r'''filename="?(?P<value>[^";]+)"?''', content_disposition, re.IGNORECASE)
        if filename_match:
            value = str(filename_match.group("value") or "").strip()
            if value:
                return value
        path_name = Path(urllib.parse.urlparse(str(fallback_url or "").strip()).path).name
        return path_name or "download.bin"

    def _download_via_http_fallback(self, *, page: Any, source_url: str, target_dir: Path) -> Path:
        resolved_url = str(source_url or "").strip()
        if not resolved_url:
            raise BrowserOpError("The browser download fallback requires a concrete source URL.")
        self._enforce_scope(resolved_url, self._session.allowed_domains)

        cookies: dict[str, str] = {}
        try:
            for cookie in page.context.cookies([resolved_url]):
                name = str((cookie or {}).get("name") or "").strip()
                value = str((cookie or {}).get("value") or "").strip()
                if name:
                    cookies[name] = value
        except Exception:  # pragma: no cover - best effort only
            cookies = {}

        try:
            response = requests.get(resolved_url, allow_redirects=True, stream=True, timeout=20, cookies=cookies)
            response.raise_for_status()
        except Exception as exc:
            raise BrowserOpError(f"Browser download fallback failed for {resolved_url}: {exc}") from exc

        filename = self._filename_from_download_response(response, resolved_url)
        save_path = target_dir / filename
        with save_path.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    fh.write(chunk)
        return save_path

    def _capture_playwright_state(self) -> dict[str, Any]:
        page = self._ensure_playwright_page(restore_current_url=False)
        body_text = ""
        try:
            body_text = str(page.locator("body").inner_text(timeout=5000) or "").strip()
        except Exception:  # pragma: no cover - best effort only
            body_text = ""
        current_url = str(page.url or "").strip()
        current_title = str(page.title() or "").strip()
        self._session.backend = "playwright"
        self._session.current_url = current_url
        self._session.current_title = current_title
        self._session.page_text = body_text
        self._session.nodes = []
        element_inventory = self._capture_playwright_element_inventory()
        return {
            "current_url": current_url,
            "title": current_title,
            "text_preview": body_text[:400],
            "element_inventory": element_inventory,
        }

    def exec_browser_op(
        self,
        payload_text: str,
        *,
        cancel_token: CancellationToken | None = None,
    ) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_token)
        try:
            payload = json.loads(str(payload_text or "").strip())
        except Exception as exc:
            return self._result(
                status="FAILED",
                action="",
                summary=f"Invalid BROWSER_OP payload: {exc}",
            )
        if not isinstance(payload, dict):
            return self._result(
                status="FAILED",
                action="",
                summary="Invalid BROWSER_OP payload: top-level JSON object required.",
            )

        action = str(payload.get("action") or "").strip().lower()
        if not action:
            return self._result(status="FAILED", action="", summary="BROWSER_OP requires an 'action' field.")
        if not bool(getattr(CFG, "COMPUTER_USE_ENABLED", True)):
            return self._result(
                status="BLOCKED",
                action=action,
                summary="Browser action blocked: computer use is disabled by configuration.",
            )

        try:
            if action not in {"goto_url", "open_page"} and not str(self._session.current_url or "").strip():
                self._ensure_active_page_for_action(payload, cancel_token=cancel_token)
            handler = getattr(self, f"_handle_{action}", None)
            if handler is None:
                raise BrowserOpError(f"Unsupported BROWSER_OP action: {action}")
            result = handler(payload, cancel_token=cancel_token)
            self._raise_if_cancelled(cancel_token)
            return result
        except BrowserScopeError as exc:
            return self._result(status="BLOCKED", action=action, summary=str(exc), backend=self._session.backend or "")
        except BrowserOpError as exc:
            return self._result(status="FAILED", action=action, summary=str(exc), backend=self._session.backend or "")
        except PlaywrightTimeoutError as exc:  # pragma: no cover - depends on local install
            return self._result(status="FAILED", action=action, summary=f"Browser action timed out: {exc}", backend="playwright")
        except PlaywrightError as exc:  # pragma: no cover - depends on local install
            return self._result(status="FAILED", action=action, summary=f"Browser automation error: {exc}", backend="playwright")
        except Exception as exc:
            return self._result(status="FAILED", action=action, summary=f"Unexpected browser action failure: {exc}", backend=self._session.backend or "")

    def _ensure_active_page_for_action(
        self,
        payload: dict[str, Any],
        *,
        cancel_token: CancellationToken | None = None,
    ) -> None:
        start_url = str(payload.get("start_url") or "").strip()
        if not start_url:
            return
        bootstrap_payload: dict[str, Any] = {"action": "goto_url", "url": start_url}
        allowed_domains = payload.get("allowed_domains")
        if isinstance(allowed_domains, list) and allowed_domains:
            bootstrap_payload["allowed_domains"] = allowed_domains
        result = self._handle_goto_url(bootstrap_payload, cancel_token=cancel_token)
        if str(result.get("status") or "").upper() != "EXECUTED":
            raise BrowserOpError(str(result.get("summary") or "Could not open the requested browser page."))

    def _handle_open_page(self, payload: dict[str, Any], *, cancel_token: CancellationToken | None = None) -> dict[str, Any]:
        return self._handle_goto_url(payload, cancel_token=cancel_token)

    def _handle_goto_url(self, payload: dict[str, Any], *, cancel_token: CancellationToken | None = None) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_token)
        url = str(payload.get("url") or "").strip()
        if not url:
            raise BrowserOpError("BROWSER_OP goto_url requires a non-empty 'url'.")
        allowed_domains = self._normalize_domains(payload.get("allowed_domains"))
        if _FILE_SCHEME_RE.match(url):
            self._session.allowed_domains = []
            self._load_local_page(url)
            return self._result(
                status="EXECUTED",
                action="goto_url",
                summary=f"Opened local browser page: {self._session.current_url}",
                backend=self._session.backend,
                current_url=self._session.current_url,
                title=self._session.current_title,
                text_preview=self._session.page_text[:400],
                element_inventory=self._build_local_element_inventory(),
                field_values=dict(self._session.field_values),
                verification={
                    "kind": "browser_state",
                    "current_url": self._session.current_url,
                    "title": self._session.current_title,
                },
            )

        self._enforce_scope(url, allowed_domains)
        page = self._ensure_playwright_page()
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        self._session.allowed_domains = allowed_domains
        state = self._capture_playwright_state()
        self._enforce_scope(str(state.get("current_url") or url), self._session.allowed_domains)
        return self._result(
            status="EXECUTED",
            action="goto_url",
            summary=f"Opened browser page: {state.get('current_url') or url}",
            backend="playwright",
            verification={
                "kind": "browser_state",
                "current_url": state.get("current_url") or "",
                "title": state.get("title") or "",
            },
            **state,
        )

    def _handle_capture_state(self, payload: dict[str, Any], *, cancel_token: CancellationToken | None = None) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_token)
        del payload
        if self._session.backend == "playwright":
            state = self._capture_playwright_state()
            return self._result(
                status="EXECUTED",
                action="capture_state",
                summary="Captured browser state from the active page.",
                backend="playwright",
                field_values=dict(self._session.field_values),
                verification={"kind": "browser_state", "current_url": state.get("current_url") or "", "title": state.get("title") or ""},
                **state,
            )
        if not self._session.current_url:
            raise BrowserOpError("No active browser page is loaded yet.")
        return self._result(
            status="EXECUTED",
            action="capture_state",
            summary="Captured browser state from the active local page.",
            backend=self._session.backend,
            current_url=self._session.current_url,
            title=self._session.current_title,
            text_preview=self._session.page_text[:400],
            element_inventory=self._build_local_element_inventory(),
            field_values=dict(self._session.field_values),
            verification={"kind": "browser_state", "current_url": self._session.current_url, "title": self._session.current_title},
        )

    def _handle_extract_text(self, payload: dict[str, Any], *, cancel_token: CancellationToken | None = None) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_token)
        selector = str(payload.get("selector") or "").strip()
        text_hint = str(payload.get("text") or "").strip()
        topic = str(payload.get("topic") or "").strip()
        avoid_heading = str(payload.get("avoid_heading") or "").strip()
        if self._session.backend == "playwright":
            topic_result: dict[str, Any] | None = None
            if topic:
                topic_result = self._extract_topic_text_from_blocks(
                    blocks=self._capture_playwright_text_blocks(),
                    title=str(self._session.current_title or ""),
                    topic=topic,
                    selector_hint=selector,
                    text_hint=text_hint,
                    avoid_heading=avoid_heading,
                )
            if topic_result is not None:
                extracted = str(topic_result.get("extracted_text") or "").strip()
                selector_value = str(topic_result.get("selector") or selector or "body").strip()
                matched_heading = str(topic_result.get("matched_heading") or "").strip()
                strategy = str(topic_result.get("extraction_strategy") or "topic_ranked_extract").strip()
                topic_match_score = int(topic_result.get("topic_match_score") or 0)
            else:
                page = self._ensure_playwright_page()
                selector_value = self._selector_for_playwright(payload)
                locator = page.locator(selector_value).first
                extracted = str(locator.inner_text(timeout=5000) or "").strip()
                if not extracted and selector.startswith("#"):
                    field_key = selector
                    extracted = str(self._session.field_values.get(field_key) or "").strip()
                if not extracted:
                    raise BrowserOpError(f"No text could be extracted for selector: {selector_value}")
                matched_heading = ""
                strategy = "selector_extract"
                topic_match_score = 0
            state = self._capture_playwright_state()
            result = self._result(
                status="EXECUTED",
                action="extract_text",
                summary="Extracted text from the active browser page.",
                backend="playwright",
                extracted_text=extracted,
                selector=selector_value,
                selector_strategy=strategy,
                verification={
                    "kind": "text_extract",
                    "selector": selector_value,
                    "extracted_text": extracted,
                    "current_url": state.get("current_url") or "",
                },
                **state,
            )
            if topic:
                result["topic"] = topic
                result["topic_match_score"] = topic_match_score
            if matched_heading:
                result["matched_heading"] = matched_heading
            # When topic suggests a download/file search, enrich element_inventory
            # with href-bearing anchor candidates so the planner can see actual download URLs.
            _DOWNLOAD_TOPIC_TOKENS = {"download", "format", "file", "txt", "pdf", "text", "html"}
            topic_tokens = set(re.findall(r"[a-z]+", topic.lower())) if topic else set()
            if topic and topic_tokens & _DOWNLOAD_TOPIC_TOKENS:
                dl_candidates = self._capture_playwright_download_candidates()
                dl_entries = [
                    {k: v for k, v in c.items() if k in ("tag", "selector", "href", "text", "id")}
                    for c in dl_candidates[:12]
                    if c.get("href")
                ]
                if dl_entries:
                    seen_hrefs = {e.get("href") for e in dl_entries}
                    existing = [e for e in (result.get("element_inventory") or []) if e.get("href") not in seen_hrefs]
                    result["element_inventory"] = dl_entries + existing
            return result
        if not self._session.current_url:
            raise BrowserOpError("No active browser page is loaded yet.")
        matched_heading = ""
        topic_match_score = 0
        if topic:
            topic_result = self._extract_topic_text_from_blocks(
                blocks=self._build_local_topic_blocks(),
                title=self._session.current_title,
                topic=topic,
                selector_hint=selector,
                text_hint=text_hint,
                avoid_heading=avoid_heading,
            )
        else:
            topic_result = None
        if topic_result is not None:
            extracted = str(topic_result.get("extracted_text") or "").strip()
            selector = str(topic_result.get("selector") or selector or "body").strip()
            strategy = str(topic_result.get("extraction_strategy") or "topic_ranked_extract").strip()
            matched_heading = str(topic_result.get("matched_heading") or "").strip()
            topic_match_score = int(topic_result.get("topic_match_score") or 0)
        elif not selector and not text_hint:
            extracted = self._session.page_text
            strategy = "page_text"
        elif selector.lower() in {"body", "html"}:
            extracted = self._session.page_text
            strategy = "page_text"
        else:
            node, strategy = self._find_node(selector=selector, text=text_hint)
            if node is None:
                raise BrowserOpError(f"Could not find a browser element for selector/text: {selector or text_hint}")
            extracted = node.text
            if not extracted:
                extracted = str(self._session.field_values.get(self._selector_key(selector, node)) or "").strip()
        if not extracted:
            raise BrowserOpError("The targeted browser element did not yield any text.")
        result = self._result(
            status="EXECUTED",
            action="extract_text",
            summary="Extracted text from the active local browser page.",
            backend=self._session.backend,
            extracted_text=extracted,
            selector=selector,
            selector_strategy=strategy,
            current_url=self._session.current_url,
            title=self._session.current_title,
            text_preview=self._session.page_text[:400],
            element_inventory=self._build_local_element_inventory(),
            field_values=dict(self._session.field_values),
            verification={
                "kind": "text_extract",
                "selector": selector or text_hint,
                "extracted_text": extracted,
                "current_url": self._session.current_url,
            },
        )
        if topic:
            result["topic"] = topic
            result["topic_match_score"] = topic_match_score
        if matched_heading:
            result["matched_heading"] = matched_heading
        return result

    def _handle_wait_for(self, payload: dict[str, Any], *, cancel_token: CancellationToken | None = None) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_token)
        selector = str(payload.get("selector") or "").strip()
        text_hint = str(payload.get("text") or "").strip()
        if not selector and not text_hint:
            raise BrowserOpError("BROWSER_OP wait_for requires 'selector' or 'text'.")
        if self._session.backend == "playwright":
            page = self._ensure_playwright_page()
            if selector:
                page.locator(self._selector_for_playwright(payload)).first.wait_for(state="visible", timeout=5000)
            else:
                page.get_by_text(text_hint).first.wait_for(state="visible", timeout=5000)
            state = self._capture_playwright_state()
            return self._result(
                status="EXECUTED",
                action="wait_for",
                summary="Verified the expected browser element/text is present.",
                backend="playwright",
                verification={
                    "kind": "presence",
                    "selector": selector or f"text={text_hint}",
                    "current_url": state.get("current_url") or "",
                },
                **state,
            )
        node, strategy = self._find_node(selector=selector, text=text_hint)
        if node is None:
            raise BrowserOpError(f"Expected browser target is not present yet: {selector or text_hint}")
        return self._result(
            status="EXECUTED",
            action="wait_for",
            summary="Verified the expected local browser element/text is present.",
            backend=self._session.backend,
            selector=selector or text_hint,
            selector_strategy=strategy,
            current_url=self._session.current_url,
            title=self._session.current_title,
            text_preview=self._session.page_text[:400],
            element_inventory=self._build_local_element_inventory(),
            field_values=dict(self._session.field_values),
            verification={
                "kind": "presence",
                "selector": selector or text_hint,
                "current_url": self._session.current_url,
            },
        )

    def _handle_click(self, payload: dict[str, Any], *, cancel_token: CancellationToken | None = None) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_token)
        selector = str(payload.get("selector") or "").strip()
        text_hint = str(payload.get("text") or "").strip()
        if self._session.backend == "playwright":
            page = self._ensure_playwright_page()
            selector_value = self._download_selector_for_playwright(payload)
            locator = page.locator(selector_value).first
            locator.click(timeout=5000)
            state = self._capture_playwright_state()
            self._enforce_scope(str(state.get("current_url") or ""), self._session.allowed_domains)
            return self._result(
                status="EXECUTED",
                action="click",
                summary="Clicked the requested browser element.",
                backend="playwright",
                selector=selector_value,
                verification={
                    "kind": "post_click_state",
                    "selector": selector_value,
                    "current_url": state.get("current_url") or "",
                },
                **state,
            )

        node, strategy = self._find_node(selector=selector, text=text_hint)
        if node is None:
            raise BrowserOpError(f"Could not find a browser element to click: {selector or text_hint}")
        target = str(node.attrs.get("href") or node.attrs.get("data-href") or "").strip()
        if not target:
            raise BrowserOpError("The targeted local browser element does not define a navigation target.")
        next_url = urllib.parse.urljoin(self._session.current_url, target)
        if _HTTP_SCHEME_RE.match(next_url):
            self._enforce_scope(next_url, self._session.allowed_domains)
        if _FILE_SCHEME_RE.match(next_url):
            self._load_local_page(next_url)
        else:
            raise BrowserOpError(
                "Local browser fixtures only support file:// navigation targets in this environment."
            )
        return self._result(
            status="EXECUTED",
            action="click",
            summary=f"Clicked the requested browser element and navigated to {self._session.current_url}.",
            backend=self._session.backend,
            selector=selector or text_hint,
            selector_strategy=strategy,
            current_url=self._session.current_url,
            title=self._session.current_title,
            text_preview=self._session.page_text[:400],
            element_inventory=self._build_local_element_inventory(),
            field_values=dict(self._session.field_values),
            verification={
                "kind": "post_click_state",
                "selector": selector or text_hint,
                "current_url": self._session.current_url,
                "title": self._session.current_title,
            },
        )

    def _handle_type_text(self, payload: dict[str, Any], *, cancel_token: CancellationToken | None = None) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_token)
        selector = str(payload.get("selector") or "").strip()
        text_value = str(payload.get("text") or payload.get("value") or "").strip()
        if not selector:
            raise BrowserOpError("BROWSER_OP type_text requires 'selector'.")
        if not text_value:
            raise BrowserOpError("BROWSER_OP type_text requires non-empty 'text' or 'value'.")
        if self._session.backend == "playwright":
            page = self._ensure_playwright_page()
            selector_value = self._selector_for_playwright(payload)
            locator = page.locator(selector_value).first
            locator.fill(text_value, timeout=5000)
            try:
                field_value = str(locator.input_value(timeout=5000) or "").strip()
            except Exception:  # pragma: no cover - best effort only
                field_value = text_value
            self._session.field_values[selector] = field_value
            state = self._capture_playwright_state()
            return self._result(
                status="EXECUTED",
                action="type_text",
                summary="Typed text into the requested browser field.",
                backend="playwright",
                selector=selector_value,
                field_value=field_value,
                verification={
                    "kind": "field_value",
                    "selector": selector_value,
                    "field_value": field_value,
                    "current_url": state.get("current_url") or "",
                },
                **state,
            )

        node, strategy = self._find_node(selector=selector)
        if node is None:
            raise BrowserOpError(f"Could not find a browser field for selector: {selector}")
        field_key = self._selector_key(selector, node)
        self._session.field_values[field_key] = text_value
        self._session.field_values[selector] = text_value
        return self._result(
            status="EXECUTED",
            action="type_text",
            summary="Typed text into the requested local browser field.",
            backend=self._session.backend,
            selector=selector,
            selector_strategy=strategy,
            field_value=text_value,
            current_url=self._session.current_url,
            title=self._session.current_title,
            text_preview=self._session.page_text[:400],
            element_inventory=self._build_local_element_inventory(),
            field_values=dict(self._session.field_values),
            verification={
                "kind": "field_value",
                "selector": selector,
                "field_value": text_value,
                "current_url": self._session.current_url,
            },
        )

    def _handle_download(self, payload: dict[str, Any], *, cancel_token: CancellationToken | None = None) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_token)
        selector = str(payload.get("selector") or "").strip()
        text_hint = str(payload.get("text") or "").strip()
        download_dir = str(payload.get("download_dir") or "").strip()
        if not download_dir:
            raise BrowserOpError("BROWSER_OP download requires a workspace-relative 'download_dir'.")
        target_dir = (self.workspace / download_dir).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        if self._session.backend == "playwright":
            page = self._ensure_playwright_page()
            selector_value = self._download_selector_for_playwright(payload)
            locator = page.locator(selector_value).first
            download_label = ""
            source_href = ""
            download_attr = ""
            try:
                download_label = self._compact_text(str(locator.inner_text(timeout=3000) or ""), limit=180)
            except Exception:  # pragma: no cover - best effort only
                download_label = ""
            try:
                source_href = str(locator.get_attribute("href", timeout=3000) or "").strip()
            except Exception:  # pragma: no cover - best effort only
                source_href = ""
            try:
                download_attr = str(locator.get_attribute("download", timeout=3000) or "").strip()
            except Exception:  # pragma: no cover - best effort only
                download_attr = ""
            try:
                with page.expect_download(timeout=10000) as download_info:
                    locator.click(timeout=5000)
                download = download_info.value
                suggested_name = str(download.suggested_filename or "download.bin").strip() or "download.bin"
                save_path = target_dir / suggested_name
                download.save_as(str(save_path))
            except PlaywrightTimeoutError:
                resolved_url = urllib.parse.urljoin(str(page.url or "").strip(), source_href) if source_href else ""
                if not resolved_url:
                    raise
                if self._looks_like_html_page_href(source_href, download_attr):
                    raise BrowserOpError("The targeted browser element points to a page, not a downloadable artifact.")
                save_path = self._download_via_http_fallback(page=page, source_url=resolved_url, target_dir=target_dir)
            state = self._capture_playwright_state()
            result = self._result(
                status="EXECUTED",
                action="download",
                summary=f"Downloaded the requested browser artifact to {save_path.relative_to(self.workspace).as_posix()}",
                backend="playwright",
                selector=selector_value,
                saved_path=save_path.relative_to(self.workspace).as_posix(),
                verification={
                    "kind": "download",
                    "saved_path": save_path.relative_to(self.workspace).as_posix(),
                    "current_url": state.get("current_url") or "",
                },
                **state,
            )
            if download_label:
                result["download_label"] = download_label
            if source_href:
                result["source_href"] = source_href
            return result

        node, strategy = self._find_local_download_node(selector=selector, text=text_hint)
        if node is None:
            raise BrowserOpError(f"Could not find a browser download element: {selector or text_hint}")
        target = str(node.attrs.get("href") or node.attrs.get("data-href") or "").strip()
        if not target:
            raise BrowserOpError("The targeted local browser element does not define a downloadable href.")
        if not self._is_local_downloadable_href(target, node):
            raise BrowserOpError("The targeted local browser element points to a page, not a downloadable artifact.")
        source_url = urllib.parse.urljoin(self._session.current_url, target)
        source_path = self._resolve_file_url(source_url)
        if not source_path.exists() or not source_path.is_file():
            raise BrowserOpError(f"Local download source not found: {source_path}")
        save_path = target_dir / source_path.name
        shutil.copy2(source_path, save_path)
        result = self._result(
            status="EXECUTED",
            action="download",
            summary=f"Downloaded the requested local browser artifact to {save_path.relative_to(self.workspace).as_posix()}",
            backend=self._session.backend,
            selector=selector or self._node_selector_hint(node),
            selector_strategy=strategy,
            saved_path=save_path.relative_to(self.workspace).as_posix(),
            verification={
                "kind": "download",
                "saved_path": save_path.relative_to(self.workspace).as_posix(),
                "current_url": self._session.current_url,
            },
        )
        download_label = self._compact_text(node.text, limit=180)
        if download_label:
            result["download_label"] = download_label
        result["source_href"] = target
        return result
