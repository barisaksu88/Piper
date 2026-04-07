from __future__ import annotations

import re
from typing import Any

from core.engines.verification import VerificationResult


_GENERIC_BROWSER_SELECTORS = {"", "body", "html"}
_ATTR_SELECTOR_RE = re.compile(r"""^\[(?P<name>[a-zA-Z0-9_-]+)=['"](?P<value>[^'"]+)['"]\]$""")
_DOWNLOAD_HINT_STOPWORDS = {"the", "a", "an", "file", "artifact", "download", "version"}
_DOWNLOAD_TOKEN_ALIASES = {
    "archive": {".zip", ".tar", ".tar.gz", ".tgz", ".gz", ".bz2", ".xz", "archive"},
    "checksum": {".sha1", ".sha256", ".sha512", "checksum", "md5", "sha1", "sha256", "sha512", "sig", "signature"},
    "html": {".htm", ".html", "htm", "html"},
    "installer": {".deb", ".dmg", ".exe", ".msi", ".pkg", ".rpm", "install", "installer", "setup"},
    "pdf": {".pdf", "pdf"},
    "source": {".tar", ".tar.gz", ".tgz", ".zip", "source", "src"},
    "text": {".md", ".rst", ".txt", "plain", "readme", "text", "txt"},
}


def new_stage_evidence(stage: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = (stage or {}).get("computer_use") or {}
    return {
        "start_url": str(meta.get("start_url") or "").strip(),
        "current_url": "",
        "title": "",
        "actions": [],
        "extracts": [],
        "downloads": [],
        "download_details": [],
        "field_values": {},
        "element_inventory": [],
    }


def update_stage_evidence(evidence: dict[str, Any], tool_result: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        evidence = {}
    if not isinstance(tool_result, dict):
        return evidence
    if str(tool_result.get("tool") or "").upper() != "BROWSER_OP":
        return evidence
    if str(tool_result.get("status") or "").upper() != "EXECUTED":
        return evidence

    action = str(tool_result.get("action") or "").strip().lower()
    if action:
        actions = evidence.setdefault("actions", [])
        if action not in actions:
            actions.append(action)

    current_url = str(tool_result.get("current_url") or "").strip()
    if current_url:
        evidence["current_url"] = current_url
    title = str(tool_result.get("title") or "").strip()
    if title:
        evidence["title"] = title

    selector = str(tool_result.get("selector") or "").strip()
    extracted_text = str(tool_result.get("extracted_text") or "").strip()
    if extracted_text:
        extracts = evidence.setdefault("extracts", [])
        entry = {
            "selector": selector,
            "text": extracted_text,
        }
        topic = str(tool_result.get("topic") or "").strip()
        matched_heading = str(tool_result.get("matched_heading") or "").strip()
        selector_strategy = str(tool_result.get("selector_strategy") or "").strip()
        if topic:
            entry["topic"] = topic
        if matched_heading:
            entry["matched_heading"] = matched_heading
        if selector_strategy:
            entry["selector_strategy"] = selector_strategy
        topic_match_score = tool_result.get("topic_match_score")
        if isinstance(topic_match_score, (int, float)):
            entry["topic_match_score"] = int(topic_match_score)
        extracts.append(entry)

    saved_path = str(tool_result.get("saved_path") or "").strip().replace("\\", "/")
    if saved_path:
        downloads = evidence.setdefault("downloads", [])
        if saved_path not in downloads:
            downloads.append(saved_path)
        download_details = evidence.setdefault("download_details", [])
        detail_entry = {
            "saved_path": saved_path,
            "selector": str(tool_result.get("selector") or "").strip(),
            "label": str(tool_result.get("download_label") or "").strip(),
            "href": str(tool_result.get("source_href") or "").strip(),
        }
        if detail_entry not in download_details:
            download_details.append(detail_entry)

    field_values = evidence.setdefault("field_values", {})
    selector_value = str(tool_result.get("selector") or "").strip()
    field_value = str(tool_result.get("field_value") or "").strip()
    if selector_value and field_value:
        field_values[selector_value] = field_value
        for item in tool_result.get("element_inventory") or []:
            if not isinstance(item, dict):
                continue
            item_selector = str(item.get("selector") or "").strip()
            if item_selector != selector_value:
                continue
            for alias in _field_aliases_for_inventory_item(item):
                field_values[alias] = field_value
    for key, value in (tool_result.get("field_values") or {}).items():
        key_s = str(key or "").strip()
        value_s = str(value or "").strip()
        if key_s and value_s:
            field_values[key_s] = value_s

    inventory = evidence.setdefault("element_inventory", [])
    for item in tool_result.get("element_inventory") or []:
        if not isinstance(item, dict):
            continue
        selector_hint = str(item.get("selector") or "").strip()
        if not selector_hint:
            continue
        normalized = {k: str(v).strip() for k, v in item.items() if str(v or "").strip()}
        if normalized and normalized not in inventory:
            inventory.append(normalized)

    return evidence


def evaluate_stage(stage: dict[str, Any], evidence: dict[str, Any]) -> VerificationResult:
    meta = (stage or {}).get("computer_use") or {}
    missing: list[str] = []
    satisfied: list[str] = []

    downloads = [str(item).strip().replace("\\", "/") for item in (evidence.get("downloads") or []) if str(item).strip()]
    download_details = [item for item in (evidence.get("download_details") or []) if isinstance(item, dict)]
    extracts = [item for item in (evidence.get("extracts") or []) if isinstance(item, dict)]
    field_values = {
        str(key).strip(): str(value).strip()
        for key, value in (evidence.get("field_values") or {}).items()
        if str(key).strip() and str(value).strip()
    }
    inventory = [item for item in (evidence.get("element_inventory") or []) if isinstance(item, dict)]

    require_download = bool(meta.get("require_download"))
    require_extract = bool(meta.get("require_extract"))
    require_form_fill = bool(meta.get("require_form_fill"))
    require_navigation = bool(meta.get("require_navigation"))
    report_title = bool(meta.get("report_title"))
    report_status_text = bool(meta.get("report_status_text"))

    if require_download:
        required_dir = str(meta.get("download_dir") or "").strip().replace("\\", "/").rstrip("/")
        download_hint = str(meta.get("download_hint") or "").strip()
        matched_download = ""
        matched_detail: dict[str, Any] = {}
        matched_score = -10**9
        for detail in download_details:
            path = str(detail.get("saved_path") or "").strip().replace("\\", "/")
            if not path:
                continue
            if required_dir and not (path == required_dir or path.startswith(required_dir + "/")):
                continue
            score = _score_download_detail(detail, download_hint) if download_hint else 0
            if download_hint and score < 28:
                continue
            if score > matched_score:
                matched_download = path
                matched_detail = detail
                matched_score = score
        if not matched_download:
            best_path_score = -10**9
            for path in downloads:
                if not required_dir or path == required_dir or path.startswith(required_dir + "/"):
                    score = _score_download_path(path, download_hint) if download_hint else 0
                    if download_hint and score < 28:
                        continue
                    if score > best_path_score:
                        matched_download = path
                        best_path_score = score
        if matched_download:
            if download_hint:
                detail_label = str(matched_detail.get("label") or "").strip()
                if detail_label:
                    satisfied.append(f"downloaded '{detail_label}' to {matched_download}")
                else:
                    satisfied.append(f"downloaded artifact matching '{download_hint}' to {matched_download}")
            else:
                satisfied.append(f"downloaded artifact saved to {matched_download}")
        else:
            if download_hint:
                missing.append(
                    f"download the requested artifact matching '{download_hint}' into '{required_dir or 'the workspace download folder'}'"
                )
            else:
                missing.append(
                    f"download the requested artifact into '{required_dir or 'the workspace download folder'}'"
                )

    if require_form_fill:
        selector_hint = str(meta.get("selector_hint") or "").strip()
        input_text = str(meta.get("input_text") or "").strip()
        has_match = False
        if selector_hint and input_text:
            has_match = _field_value_matches(
                selector_hint=selector_hint,
                input_text=input_text,
                field_values=field_values,
                inventory=inventory,
            )
        elif input_text:
            has_match = input_text in set(field_values.values())
        else:
            has_match = "type_text" in {str(item).strip().lower() for item in (evidence.get("actions") or [])}
        if has_match:
            if input_text:
                satisfied.append(f"verified field value {input_text}")
            else:
                satisfied.append("completed the requested form input")
        else:
            if selector_hint and input_text:
                missing.append(f"fill '{selector_hint}' with {input_text!r}")
            else:
                missing.append("complete the requested form interaction")

    if require_navigation:
        start_url = str(meta.get("start_url") or evidence.get("start_url") or "").strip()
        current_url = str(evidence.get("current_url") or "").strip()
        actions = {str(item).strip().lower() for item in (evidence.get("actions") or [])}
        navigated = bool(current_url and current_url != start_url and "click" in actions)
        if navigated:
            satisfied.append(f"navigated to {current_url}")
        else:
            missing.append("complete the requested navigation step")

    if require_extract:
        matched_extract = _match_required_extract(meta, extracts, inventory, title=str(evidence.get("title") or "").strip())
        if matched_extract:
            satisfied.append(matched_extract)
        else:
            if report_title:
                missing.append("report the verified page title")
            elif report_status_text:
                missing.append("extract the requested status text from the page")
            else:
                missing.append("extract the requested on-page information")

    if not require_download and not require_extract and not require_form_fill and not require_navigation:
        current_url = str(evidence.get("current_url") or "").strip()
        if current_url:
            satisfied.append(f"opened browser page {current_url}")
        else:
            missing.append("open the requested browser page")

    if not missing:
        return VerificationResult.verified(_build_summary(satisfied), checker_path="RULES")

    if satisfied or extracts or downloads or field_values or inventory or evidence.get("current_url"):
        detail = "Partial browser progress: " + "; ".join(satisfied) if satisfied else "Partial browser progress recorded."
        return VerificationResult.partial(
            f"{detail} Missing: {', '.join(missing)}.",
            retry_budget=1,
            checker_path="RULES",
        )

    return VerificationResult.failed(f"Browser stage still lacks required evidence: {', '.join(missing)}.", checker_path="RULES")


def build_verified_payload(stage: dict[str, Any], evidence: dict[str, Any], verification: VerificationResult) -> dict[str, Any]:
    meta = (stage or {}).get("computer_use") or {}
    extracts = [item for item in (evidence.get("extracts") or []) if isinstance(item, dict)]
    downloads = [str(item).strip().replace("\\", "/") for item in (evidence.get("downloads") or []) if str(item).strip()]
    download_details = [item for item in (evidence.get("download_details") or []) if isinstance(item, dict)]
    expected_text = str(meta.get("expected_text") or "").strip().lower()
    selector_hint = str(meta.get("selector_hint") or "").strip()
    requested_topic = str(meta.get("requested_topic") or "").strip()
    download_hint = str(meta.get("download_hint") or "").strip()
    inventory = [item for item in (evidence.get("element_inventory") or []) if isinstance(item, dict)]
    payload: dict[str, Any] = {
        "summary": str(verification.evidence_summary or "").strip(),
        "current_url": str(evidence.get("current_url") or "").strip(),
        "title": str(evidence.get("title") or "").strip(),
        "downloads": downloads,
        "field_values": {
            str(key).strip(): str(value).strip()
            for key, value in (evidence.get("field_values") or {}).items()
            if str(key).strip() and str(value).strip()
        },
        "extracts": [
            {
                "selector": str(item.get("selector") or "").strip(),
                "text": str(item.get("text") or "").strip(),
                "topic": str(item.get("topic") or "").strip(),
                "matched_heading": str(item.get("matched_heading") or "").strip(),
            }
            for item in extracts
            if str(item.get("text") or "").strip()
        ],
        "element_inventory": [
            {
                "tag": str(item.get("tag") or "").strip(),
                "selector": str(item.get("selector") or "").strip(),
                "text": str(item.get("text") or "").strip(),
            }
            for item in inventory
            if str(item.get("selector") or "").strip() or str(item.get("text") or "").strip()
        ],
    }
    if requested_topic:
        payload["requested_topic"] = requested_topic
    if download_hint:
        payload["download_hint"] = download_hint
    if meta.get("report_status_text"):
        status_selector = _status_selector_candidates(evidence.get("element_inventory") or [])
        for item in extracts:
            selector = str(item.get("selector") or "").strip()
            text_value = str(item.get("text") or "").strip()
            if not text_value:
                continue
            if selector in status_selector or "status" in selector.lower():
                payload["status_text"] = text_value
                break
        if "status_text" not in payload:
            inventory_text = _inventory_text_for_token(inventory, "status")
            if inventory_text:
                payload["status_text"] = inventory_text
    if expected_text and "status_text" not in payload:
        token_selectors = _selector_candidates_for_token(inventory, expected_text)
        for item in extracts:
            selector = str(item.get("selector") or "").strip()
            text_value = str(item.get("text") or "").strip()
            if not text_value:
                continue
            if token_selectors and selector in token_selectors:
                payload["extracted_text"] = text_value
                break
            if not token_selectors:
                payload["extracted_text"] = text_value
                break
        if "extracted_text" not in payload:
            inventory_text = _inventory_text_for_token(inventory, expected_text)
            if inventory_text:
                payload["extracted_text"] = inventory_text
    if requested_topic and "extracted_text" not in payload and not meta.get("report_title"):
        topic_match = _extract_for_requested_topic(extracts, requested_topic)
        if topic_match:
            payload["extracted_text"] = str(topic_match.get("text") or "").strip()
            matched_heading = str(topic_match.get("matched_heading") or "").strip()
            if matched_heading:
                payload["matched_heading"] = matched_heading
    if selector_hint and "extracted_text" not in payload and not meta.get("report_title"):
        for item in extracts:
            selector = str(item.get("selector") or "").strip()
            text_value = str(item.get("text") or "").strip()
            if selector == selector_hint and text_value:
                payload["extracted_text"] = text_value
                break
    if "extracted_text" not in payload and not meta.get("report_title"):
        for item in extracts:
            text_value = str(item.get("text") or "").strip()
            if text_value:
                payload["extracted_text"] = text_value
                break
    if meta.get("report_title"):
        payload["reported_title"] = str(evidence.get("title") or "").strip()
    if downloads:
        payload["saved_path"] = downloads[-1]
    if download_hint and download_details:
        best_detail: dict[str, Any] = {}
        best_score = -10**9
        for detail in download_details:
            score = _score_download_detail(detail, download_hint)
            if score < 28:
                continue
            if score > best_score:
                best_detail = detail
                best_score = score
        if best_detail:
            label = str(best_detail.get("label") or "").strip()
            href = str(best_detail.get("href") or "").strip()
            path = str(best_detail.get("saved_path") or "").strip().replace("\\", "/")
            if label:
                payload["download_label"] = label
            if href:
                payload["source_href"] = href
            if path:
                payload["saved_path"] = path
    return payload


def _match_required_extract(meta: dict[str, Any], extracts: list[dict[str, Any]], inventory: list[dict[str, Any]], *, title: str) -> str:
    report_title = bool(meta.get("report_title"))
    report_status_text = bool(meta.get("report_status_text"))
    expected_text = str(meta.get("expected_text") or "").strip().lower()
    requested_topic = str(meta.get("requested_topic") or "").strip()
    if report_title:
        if title:
            return f"verified page title '{title}'"
        return ""

    if requested_topic:
        topic_match = _extract_for_requested_topic(extracts, requested_topic)
        if topic_match:
            return f"extracted information about '{requested_topic}'"
        return ""

    selector_hint = str(meta.get("selector_hint") or "").strip()
    if selector_hint and not bool(meta.get("require_form_fill")):
        for item in extracts:
            if str(item.get("selector") or "").strip() == selector_hint and str(item.get("text") or "").strip():
                return f"extracted text from {selector_hint}"

    if report_status_text or expected_text:
        candidate_selectors = _selector_candidates_for_token(inventory, expected_text or "status")
        for item in extracts:
            selector = str(item.get("selector") or "").strip()
            text_value = str(item.get("text") or "").strip()
            if not text_value:
                continue
            if candidate_selectors and selector in candidate_selectors:
                return f"extracted status text '{text_value}'"
            if not candidate_selectors and selector.lower() not in _GENERIC_BROWSER_SELECTORS:
                return f"extracted status text '{text_value}'"
        inventory_text = _inventory_text_for_token(inventory, expected_text or "status")
        if inventory_text:
            return f"verified on-page text '{inventory_text}'"
        return ""

    for item in extracts:
        selector = str(item.get("selector") or "").strip().lower()
        text_value = str(item.get("text") or "").strip()
        if text_value and selector not in _GENERIC_BROWSER_SELECTORS:
            return f"extracted text '{text_value}'"
    return ""


def _extract_for_requested_topic(extracts: list[dict[str, Any]], requested_topic: str) -> dict[str, Any]:
    requested_topic_l = str(requested_topic or "").strip().lower()
    if not requested_topic_l:
        return {}
    best: dict[str, Any] = {}
    best_score = -10**9
    for item in extracts:
        text_value = str(item.get("text") or "").strip()
        if not text_value:
            continue
        score = 0
        item_topic = str(item.get("topic") or "").strip().lower()
        if item_topic == requested_topic_l:
            score += 100
        if requested_topic_l and requested_topic_l in text_value.lower():
            score += 40
        matched_heading = str(item.get("matched_heading") or "").strip().lower()
        if requested_topic_l and requested_topic_l in matched_heading:
            score += 30
        selector_strategy = str(item.get("selector_strategy") or "").strip().lower()
        if selector_strategy == "topic_ranked_extract":
            score += 25
        topic_match_score = item.get("topic_match_score")
        if isinstance(topic_match_score, (int, float)):
            score += int(topic_match_score)
        if score > best_score:
            best = item
            best_score = score
    return best if best_score > 0 else {}


def _normalize_download_hint_tokens(value: str) -> list[str]:
    tokens = [token for token in re.findall(r"[a-z0-9]+", str(value or "").lower()) if token]
    normalized: list[str] = []
    for token in tokens:
        if token in _DOWNLOAD_HINT_STOPWORDS:
            continue
        if token.endswith("ies") and len(token) > 4:
            token = token[:-3] + "y"
        elif token.endswith("s") and len(token) > 4:
            token = token[:-1]
        normalized.append(token)
    return normalized


def _download_path_matches_hint(path: str, download_hint: str) -> bool:
    return _score_download_path(path, download_hint) >= 28


def _download_matches_hint(detail: dict[str, Any], download_hint: str) -> bool:
    return _score_download_detail(detail, download_hint) >= 28


def _score_download_detail(detail: dict[str, Any], download_hint: str) -> int:
    haystack = " ".join(
        [
            str(detail.get("saved_path") or ""),
            str(detail.get("selector") or ""),
            str(detail.get("label") or ""),
            str(detail.get("href") or ""),
        ]
    ).lower()
    return _score_download_hint_haystack(
        haystack,
        download_hint,
        path=str(detail.get("saved_path") or ""),
        href=str(detail.get("href") or ""),
        label=str(detail.get("label") or ""),
    )


def _score_download_path(path: str, download_hint: str) -> int:
    return _score_download_hint_haystack(
        str(path or "").strip().lower().replace("\\", "/"),
        download_hint,
        path=str(path or ""),
        href="",
        label="",
    )


def _score_download_hint_haystack(haystack: str, download_hint: str, *, path: str, href: str, label: str) -> int:
    hint_l = str(download_hint or "").strip().lower()
    hint_tokens = _normalize_download_hint_tokens(download_hint)
    if not hint_tokens:
        return 0

    score = 0
    matched = 0
    path_l = str(path or "").strip().lower().replace("\\", "/")
    href_l = str(href or "").strip().lower()
    label_l = str(label or "").strip().lower()
    if hint_l and hint_l in haystack:
        score += 80

    for token in hint_tokens:
        aliases = set(_DOWNLOAD_TOKEN_ALIASES.get(token, set()))
        aliases.add(token)
        token_score = 0
        if token in label_l:
            token_score = max(token_score, 28)
        if any(alias and alias in haystack for alias in aliases):
            token_score = max(token_score, 22)
        if token == "text" and any(value.endswith(".txt") for value in (path_l, href_l)):
            token_score = max(token_score, 44)
        elif token == "pdf" and any(value.endswith(".pdf") for value in (path_l, href_l)):
            token_score = max(token_score, 44)
        elif token == "checksum" and any(
            value.endswith(suffix)
            for value in (path_l, href_l)
            for suffix in (".sha256", ".sha512", ".sha1", ".md5", ".sig")
        ):
            token_score = max(token_score, 44)
        elif token == "archive" and any(
            value.endswith(suffix)
            for value in (path_l, href_l)
            for suffix in (".zip", ".tar", ".tar.gz", ".tgz", ".gz", ".bz2", ".xz")
        ):
            token_score = max(token_score, 40)
        elif token == "installer" and any(
            value.endswith(suffix)
            for value in (path_l, href_l)
            for suffix in (".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm")
        ):
            token_score = max(token_score, 40)
        if token_score > 0:
            matched += 1
        score += token_score

    if hint_tokens and matched:
        score += matched * 8
    if hint_tokens and matched == len(hint_tokens):
        score += 20
    if label_l in {"text", "pdf", "html"} and label_l in hint_tokens:
        score += 24
    if "checksum" not in hint_tokens and any(token in haystack for token in ("checksum", ".sha256", ".sha512", ".sha1", ".md5", ".sig")):
        score -= 40
    return score


def _status_selector_candidates(inventory: list[dict[str, Any]]) -> set[str]:
    return _selector_candidates_for_token(inventory, "status")


def _selector_candidates_for_token(inventory: list[dict[str, Any]], token: str) -> set[str]:
    needle = str(token or "").strip().lower()
    if not needle:
        return set()
    candidates: set[str] = set()
    for item in inventory:
        token_values = [
            str(item.get("selector") or "").strip(),
            str(item.get("id") or "").strip(),
            str(item.get("data_testid") or "").strip(),
            str(item.get("name") or "").strip(),
            str(item.get("text") or "").strip(),
        ]
        lowered = " ".join(part.lower() for part in token_values if part)
        if needle not in lowered:
            continue
        selector = str(item.get("selector") or "").strip()
        if selector:
            candidates.add(selector)
    return candidates


def _inventory_text_for_token(inventory: list[dict[str, Any]], token: str) -> str:
    candidate_selectors = _selector_candidates_for_token(inventory, token)
    if str(token or "").strip() and not candidate_selectors:
        return ""
    for item in inventory:
        selector = str(item.get("selector") or "").strip()
        text_value = str(item.get("text") or "").strip()
        if not text_value:
            continue
        if candidate_selectors and selector in candidate_selectors:
            return text_value
        if not candidate_selectors and selector.lower() not in _GENERIC_BROWSER_SELECTORS:
            return text_value
    return ""


def _field_value_matches(
    *,
    selector_hint: str,
    input_text: str,
    field_values: dict[str, str],
    inventory: list[dict[str, Any]],
) -> bool:
    hinted_value = str(field_values.get(selector_hint) or "").strip()
    if hinted_value == input_text:
        return True
    if input_text in set(field_values.values()):
        return True
    for item in inventory:
        if not _selector_matches_inventory_item(item, selector_hint):
            continue
        for alias in _field_aliases_for_inventory_item(item):
            if str(field_values.get(alias) or "").strip() == input_text:
                return True
    return False


def _field_aliases_for_inventory_item(item: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    selector = str(item.get("selector") or "").strip()
    if selector:
        aliases.add(selector)
    item_id = str(item.get("id") or "").strip()
    if item_id:
        aliases.add(f"#{item_id}")
    data_testid = str(item.get("data_testid") or "").strip()
    if data_testid:
        aliases.add(f"[data-testid='{data_testid}']")
    name_value = str(item.get("name") or "").strip()
    if name_value:
        aliases.add(f"[name='{name_value}']")
    return aliases


def _selector_matches_inventory_item(item: dict[str, Any], selector_hint: str) -> bool:
    hint = str(selector_hint or "").strip()
    if not hint:
        return False
    if hint in _field_aliases_for_inventory_item(item):
        return True
    if hint.startswith("#"):
        return str(item.get("id") or "").strip() == hint[1:]
    attr_match = _ATTR_SELECTOR_RE.match(hint)
    if not attr_match:
        return False
    attr_name = str(attr_match.group("name") or "").strip().lower()
    attr_value = str(attr_match.group("value") or "").strip()
    if attr_name == "data-testid":
        return str(item.get("data_testid") or "").strip() == attr_value
    if attr_name == "name":
        return str(item.get("name") or "").strip() == attr_value
    if attr_name == "id":
        return str(item.get("id") or "").strip() == attr_value
    return False


def _build_summary(parts: list[str]) -> str:
    cleaned = [str(part or "").strip() for part in parts if str(part or "").strip()]
    if not cleaned:
        return "Verified browser interaction from accumulated browser evidence."
    return "Verified browser interaction: " + "; ".join(cleaned) + "."
