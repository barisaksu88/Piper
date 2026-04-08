from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Sequence

from core.contracts import RouteDecision
from core.runtime_context import extract_latest_runtime_context_fields

_BROWSER_HTTP_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
_BROWSER_URL_RE = re.compile(r"(?P<url>(?:https?://|file://)[^\s'\"<>]+)", re.IGNORECASE)
_BROWSER_FILE_SCHEME_RE = re.compile(r"^file://", re.IGNORECASE)
_BROWSER_BARE_URL_RE = re.compile(
    r"""(?P<url>(?<!@)(?:localhost|(?:\d{1,3}\.){3}\d{1,3}|(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)(?::\d{2,5})?(?:/[^\s'"<>]*)?)""",
    re.IGNORECASE,
)
_BROWSER_REQUEST_HINT_RE = re.compile(
    r"(?i)\b(browser|website|site|web\s*page|webpage|page|tab)\b"
)
_BROWSER_ACTION_HINT_RE = re.compile(
    r"(?i)\b(open|visit|navigate|go\s+to|browse\s+to|load|click|press|type|fill|enter|submit|download|extract|read|capture)\b"
)
_BROWSER_FORM_HINT_RE = re.compile(r"(?i)\b(type|fill|enter|submit)\b")
_BROWSER_DOWNLOAD_HINT_RE = re.compile(r"(?i)\b(download)\b")
_BROWSER_EXTRACT_HINT_RE = re.compile(
    r"(?i)\b(extract|read|retrieve|get|fetch|title|text|what does it say|what's on|what is on|capture state|heading|headline)\b"
)
_BROWSER_DOWNLOAD_DIR_RE = re.compile(
    r"""(?is)\bdownload(?:\s+the\s+[\w ._-]+)?\s+(?:into|to)\s+(?P<path>"[^"]+"|'[^']+'|[A-Za-z0-9_./\\-]+)"""
)
_BROWSER_DOWNLOAD_TARGET_RE = re.compile(
    r"""(?is)\bdownload(?:\s+(?:the|a|an))?\s+(?P<target>.+?)(?:
        \s+\b(?:into|to)\b\s+(?:"[^"]+"|'[^']+'|[A-Za-z0-9_./\\-]+)
        |\s*[.?!,;]
        |\s*$
    )""",
    re.VERBOSE,
)
_BROWSER_SELECTOR_HINT_RE = re.compile(
    r"(#[A-Za-z_][\w\-]*|\[data-testid=['\"][^'\"]+['\"]\]|\[name=['\"][^'\"]+['\"]\])"
)
_BROWSER_TYPE_SELECTOR_RE = re.compile(
    r"""(?is)\b(?:type|fill|enter)\s+(?P<value>"[^"]*"|'[^']*'|.+?)\s+(?:into|in|to)\s+(?P<selector>#[A-Za-z_][\w\-]*|\[data-testid=['"][^'"]+['"]\]|\[name=['"][^'"]+['"]\])"""
)
_BROWSER_TYPE_HUMAN_SELECTOR_RE = re.compile(
    r"""(?is)\b(?:type|fill|enter)\s+(?P<value>"[^"]*"|'[^']*'|.+?)\s+(?:into|in|to)\s+(?:the\s+)?(?P<label>[A-Za-z][\w-]*)(?:\s+(?P<kind>field|input|box))\b"""
)
_BROWSER_CLICK_HUMAN_HINT_RE = re.compile(
    r"""(?is)\b(?:click|press|follow)\s+(?:the\s+)?(?P<label>[A-Za-z][\w-]*)(?:\s+(?P<kind>link|button))\b"""
)
_BROWSER_HEADING_HINT_RE = re.compile(r"(?i)\b(?:main|page)?\s*(?:heading|headline)\b")
_BROWSER_READLIKE_HINT_RE = re.compile(
    r"(?i)\b(read|show|tell|extract|retrieve|get|fetch|capture|summari[sz]e|describe|list)\b"
)
_BROWSER_WH_QUERY_RE = re.compile(r"(?i)^\s*(?:what|which|who|where|when|how)\b")
_BROWSER_MORE_OR_ELSE_RE = re.compile(r"(?i)\b(more|else|another|anything)\b")
_BROWSER_PAGE_REFERENCE_RE = re.compile(r"(?i)\b(page|site|browser|there|it|that|this)\b")
_NON_BROWSER_CONTEXT_RE = re.compile(
    r"(?i)\b(task|tasks|event|events|calendar|schedule|memory|knowledge|workspace|file|files|folder|folders|directory|directories|path|paths|filename|filenames)\b"
)
_BROWSER_CONTEXT_HISTORY_HINT_RE = re.compile(
    r"(?i)\b(browser|website|site|web\s*page|webpage|page|title|heading|documentation|docs?)\b"
)
_BROWSER_INFO_PROMPT_RE = re.compile(
    r"(?i)\b(?:which|what)\s+(?:specific\s+)?(?:piece(?:s)?\s+of\s+)?"
    r"(?:information|details?|section|sections|clause|clauses|part|parts)\b"
    r".*\b(?:extract|read|show|tell)\b"
    r"|\bextract\s+next\b"
    r"|\bwhat\s+would\s+you\s+like\s+me\s+to\s+extract\b"
)
_BROWSER_DETAIL_FOLLOWUP_RE = re.compile(
    r"(?i)\b(retrieve|get|fetch|show|tell|read|extract|summari[sz]e|describe|list|details?|information|text|content|more|else)\b"
)
_BROWSER_TOPIC_RE = re.compile(
    r"(?is)(?:"
    r"\b(?:retrieve|get|fetch|extract|summari[sz]e|describe|report|find|look\s+for)\s+"
    r"(?:the\s+)?(?:specific\s+|requested\s+)?(?P<topic>[^.?!,;]+)"
    r"|"
    r"\b(?:section|information|details?|text|content)\s+about\s+['\"]?(?P<topic_alt>[^'\".?!,;]+)"
    r")"
)
_BROWSER_REPLY_HEADING_RE = re.compile(r'(?is)\bunder\s+"(?P<heading>[^"]+)"')
_BROWSER_REPLY_MAIN_HEADING_RE = re.compile(r'(?is)\bmain heading(?:\s+at\s+\S+)?\s+is\s+"(?P<heading>[^"]+)"')
_SHORT_BROWSER_TOPIC_REPLY_MAX_TOKENS = 8
_BROWSER_DOWNLOAD_PRONOUNS = {"it", "that", "this", "them", "those", "artifact", "file"}

_COMMON_FILEISH_SUFFIXES = {
    "avif",
    "bat",
    "bmp",
    "c",
    "cfg",
    "conf",
    "cpp",
    "css",
    "csv",
    "gif",
    "go",
    "h",
    "hpp",
    "htm",
    "html",
    "ini",
    "jpeg",
    "jpg",
    "js",
    "json",
    "log",
    "md",
    "pdf",
    "png",
    "py",
    "sh",
    "sql",
    "svg",
    "toml",
    "ts",
    "txt",
    "yaml",
    "yml",
    "zip",
}


def extract_browser_url(text: str) -> str:
    match = _BROWSER_URL_RE.search(str(text or ""))
    if match:
        return _normalize_browser_url_candidate(str(match.group("url") or "").strip())
    bare_match = _BROWSER_BARE_URL_RE.search(str(text or ""))
    if bare_match:
        return _normalize_browser_url_candidate(str(bare_match.group("url") or "").strip())
    return ""


def looks_like_explicit_browser_request(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if not extract_browser_url(raw):
        return False
    return bool(
        _BROWSER_REQUEST_HINT_RE.search(raw)
        or _BROWSER_ACTION_HINT_RE.search(raw)
        or _BROWSER_EXTRACT_HINT_RE.search(raw)
        or _BROWSER_HEADING_HINT_RE.search(raw)
        or _BROWSER_FORM_HINT_RE.search(raw)
        or _BROWSER_DOWNLOAD_HINT_RE.search(raw)
    )


def build_explicit_browser_task_card(
    user_msg: str,
    url: str,
    *,
    requested_topic: str = "",
    avoid_heading: str = "",
) -> RouteDecision:
    raw_text = str(user_msg or "")
    clean_requested_topic = _clean_browser_requested_topic(requested_topic)
    clean_avoid_heading = str(avoid_heading or "").strip()
    download_hint = _extract_browser_download_hint(user_msg)
    goal_kind = _infer_browser_goal_kind(user_msg)
    selector_hint = _extract_browser_selector_hint(user_msg)
    wants_heading = bool(_BROWSER_HEADING_HINT_RE.search(raw_text))
    input_text, input_selector = _extract_browser_input_hint(user_msg)
    if input_selector and not selector_hint:
        selector_hint = input_selector
    if wants_heading and not selector_hint:
        selector_hint = "h1"
    allowed_domains = _extract_browser_allowed_domains(url)
    download_dir = _extract_browser_download_dir(user_msg)
    wants_download = bool(_BROWSER_DOWNLOAD_HINT_RE.search(raw_text))
    wants_form_fill = bool(_BROWSER_FORM_HINT_RE.search(raw_text))
    wants_click = bool(re.search(r"(?i)\b(click|press|follow|continue|next)\b", raw_text))
    wants_title = bool(re.search(r"(?i)\btitle\b", raw_text))
    wants_status = bool(re.search(r"(?i)\bstatus\b", raw_text))
    wants_extract = bool(_BROWSER_EXTRACT_HINT_RE.search(raw_text) or wants_heading)
    if wants_download and not any(
        (
            wants_title,
            wants_status,
            wants_heading,
            bool(selector_hint),
            bool(clean_requested_topic),
            wants_form_fill,
        )
    ):
        wants_extract = bool(
            re.search(
                r"(?i)\b(?:extract|read|capture\s+state|what\s+does\s+it\s+say|what(?:'s| is)\s+on)\b",
                raw_text,
            )
        )
    navigation_match = _BROWSER_CLICK_HUMAN_HINT_RE.search(raw_text)
    navigation_hint = str((navigation_match.group("label") if navigation_match else "") or "").strip().lower()
    expected_text = ""
    if wants_status:
        expected_text = "status"
    elif re.search(r"(?i)\bdestination\b", raw_text):
        expected_text = "destination"

    computer_use: dict[str, Any] = {
        "backend": "browser",
        "start_url": url,
        "allowed_domains": allowed_domains,
        "goal_kind": goal_kind,
        "require_download": wants_download,
        "require_extract": wants_extract,
        "require_form_fill": wants_form_fill,
        "require_navigation": wants_click,
        "report_title": wants_title,
        "report_status_text": wants_status,
    }
    if expected_text:
        computer_use["expected_text"] = expected_text
    if navigation_hint:
        computer_use["navigation_hint"] = navigation_hint
    if clean_requested_topic:
        computer_use["requested_topic"] = clean_requested_topic
    if clean_avoid_heading:
        computer_use["avoid_heading"] = clean_avoid_heading
    if wants_download:
        computer_use["download_dir"] = download_dir or "computer_downloads"
    if download_hint:
        computer_use["download_hint"] = download_hint
    if selector_hint:
        computer_use["selector_hint"] = selector_hint
    if input_text:
        computer_use["input_text"] = input_text

    stage_steps: list[str] = []
    success_steps: list[str] = []
    if wants_form_fill:
        if selector_hint and input_text:
            quoted_input = json.dumps(input_text, ensure_ascii=False)
            stage_steps.append(f"enter {quoted_input} into '{selector_hint}'")
            success_steps.append(f"the field '{selector_hint}' contains {quoted_input}")
        else:
            stage_steps.append("complete the requested form interaction")
            success_steps.append("the requested form interaction is verified from browser state")
    if wants_click and goal_kind != "download":
        stage_steps.append("follow the requested navigation step")
        success_steps.append("the requested navigation step is verified from browser state")
    if wants_extract:
        if wants_title:
            stage_steps.append("report the page title")
            success_steps.append("the verified browser title is reported")
        elif wants_status:
            stage_steps.append("report the requested status text")
            success_steps.append("the requested status text is extracted with verified browser state")
        elif wants_heading:
            stage_steps.append("report the page heading")
            success_steps.append("the page heading is extracted with verified browser state")
        elif selector_hint and selector_hint.lower() not in {"body", "html"} and not wants_form_fill:
            stage_steps.append(f"extract the requested text from '{selector_hint}'")
            success_steps.append(f"text from '{selector_hint}' is extracted with verified browser state")
        elif clean_requested_topic:
            stage_steps.append(f"extract the requested information about '{clean_requested_topic}'")
            success_steps.append(
                f"information about '{clean_requested_topic}' is extracted with verified browser state"
            )
        elif expected_text:
            stage_steps.append(f"report the requested {expected_text} text")
            success_steps.append(f"the requested {expected_text} text is extracted with verified browser state")
        else:
            stage_steps.append("extract the requested on-page information")
            success_steps.append("the requested on-page information is extracted with verified browser state")
    if wants_download:
        target_dir = computer_use.get("download_dir") or "computer_downloads"
        if download_hint:
            stage_steps.append(f"download the requested artifact matching '{download_hint}' into '{target_dir}'")
            success_steps.append(
                f"the requested artifact matching '{download_hint}' is downloaded into '{target_dir}' and the saved path is verified"
            )
        else:
            stage_steps.append(f"download the requested artifact into '{target_dir}'")
            success_steps.append(f"the requested artifact is downloaded into '{target_dir}' and the saved path is verified")
    if not stage_steps:
        stage_steps.append("complete the requested browser interaction")
        success_steps.append("the requested browser interaction is verified from browser state")

    stage_goal = f"Open '{url}' in the browser and {_join_browser_steps(stage_steps)}."
    success_condition = f"The page at '{url}' is loaded and {_join_browser_steps(success_steps)}."

    context = [
        "This is browser automation, not web search.",
        f"Start URL: {url}",
        "Use the COMPUTER_USE stage domain with structured BROWSER_OP actions.",
    ]
    if allowed_domains:
        context.append("Allowed domains: " + ", ".join(allowed_domains))
    else:
        context.append("Allowed domains: local file fixture only")
    if selector_hint:
        context.append(f"Selector hint: {selector_hint}")
    if input_text:
        context.append(f"Requested input text: {json.dumps(input_text, ensure_ascii=False)}")
    if clean_requested_topic:
        context.append(f"Requested topic: {clean_requested_topic}")
    if clean_avoid_heading:
        context.append(f"Avoid repeating the section under heading: {clean_avoid_heading}")
    if download_hint:
        context.append(f"Download target hint: {download_hint}")
    if expected_text:
        context.append(f"Expected text token: {expected_text}")
    if navigation_hint:
        context.append(f"Navigation hint: {navigation_hint}")
    if wants_download:
        context.append(f"Requested download dir: {computer_use.get('download_dir') or 'computer_downloads'}")
        context.append("If the page already exposes the artifact link or button, prefer BROWSER_OP download over a generic click.")
    if len(stage_steps) > 1:
        context.append("Requested browser outcomes: " + "; ".join(stage_steps))

    return {
        "decision": "TASK",
        "card": {
            "goal": f"Use the browser to complete the requested interaction at '{url}'.",
            "context": context,
            "stages": [
                {
                    "stage_goal": stage_goal,
                    "stage_type": "COMPUTER_USE",
                    "success_condition": success_condition,
                    "allowed_tools": ["BROWSER_OP"],
                    "computer_use": computer_use,
                }
            ],
        },
    }


def build_browser_context_followup_route(
    user_msg: str,
    recent_history: Sequence[dict[str, Any]],
) -> RouteDecision | None:
    text = str(user_msg or "").strip()
    if not text or text.startswith("/"):
        return None
    if extract_browser_url(text):
        return None
    if _NON_BROWSER_CONTEXT_RE.search(text):
        return None

    runtime = extract_latest_runtime_context_fields(recent_history)
    requested_topic = ""
    url = ""
    if _runtime_context_implies_browser_task(runtime):
        url = _extract_browser_url_from_runtime(runtime)
        requested_topic = _extract_browser_requested_topic(str(runtime.get("runtime_note") or ""))
    if not url:
        url, requested_topic = _extract_browser_context_from_recent_history(
            recent_history,
            current_text=text,
        )
    if not url:
        return None

    intent = _classify_browser_followup_intent(text)
    if not intent and _looks_like_browser_detail_followup(text):
        intent = "extract"
    if intent == "title":
        return build_explicit_browser_task_card(
            f"Open {url} in the browser and tell me the page title.",
            url,
            requested_topic=requested_topic,
        )
    if intent == "heading":
        return build_explicit_browser_task_card(
            f"Open {url} in the browser and tell me the main heading.",
            url,
            requested_topic=requested_topic,
        )
    if intent == "navigate":
        return build_explicit_browser_task_card(
            f"Open {url} in the browser and {text.rstrip('.!?')}.",
            url,
            requested_topic=requested_topic,
        )
    if intent == "download":
        return build_explicit_browser_task_card(
            f"Open {url} in the browser and {text.rstrip('.!?')}.",
            url,
            requested_topic=requested_topic,
        )
    if intent == "extract":
        avoid_heading = ""
        if not requested_topic and _looks_like_browser_broad_followup(text):
            requested_topic = "general info"
        if requested_topic.lower() in {"general info", "general information", "overview", "summary"}:
            avoid_heading = _extract_recent_browser_reply_heading(recent_history)
        selector_hint = _extract_browser_selector_hint(text)
        if selector_hint:
            route = build_explicit_browser_task_card(
                f"Open {url} in the browser and extract the requested text from '{selector_hint}'.",
                url,
                requested_topic=requested_topic,
                avoid_heading=avoid_heading,
            )
            return _with_browser_selector_hint(route, selector_hint)
        if requested_topic:
            route = build_explicit_browser_task_card(
                f"Open {url} in the browser and read the page text.",
                url,
                requested_topic=requested_topic,
                avoid_heading=avoid_heading,
            )
            route = _with_browser_requested_topic(route, requested_topic)
            route = _with_browser_avoid_heading(route, avoid_heading)
            return _with_browser_selector_hint(route, "body")
        route = build_explicit_browser_task_card(
            f"Open {url} in the browser and read the page text.",
            url,
            requested_topic=requested_topic,
            avoid_heading=avoid_heading,
        )
        return _with_browser_selector_hint(route, "body")
    if _looks_like_browser_topic_reply(text, recent_history):
        requested_topic = _clean_browser_requested_topic(text)
        route = build_explicit_browser_task_card(
            f"Open {url} in the browser and read the page text.",
            url,
            requested_topic=requested_topic,
        )
        route = _with_browser_requested_topic(route, requested_topic)
        return _with_browser_selector_hint(route, "body")
    return None


def _normalize_browser_url_candidate(raw_value: str) -> str:
    candidate = str(raw_value or "").strip().rstrip(".,;!?")
    if not candidate:
        return ""
    if _BROWSER_FILE_SCHEME_RE.match(candidate):
        return candidate
    if _BROWSER_HTTP_SCHEME_RE.match(candidate):
        return candidate

    probe = urllib.parse.urlparse(f"http://{candidate}")
    host = str(probe.hostname or "").strip().lower()
    if not host:
        return ""
    if not re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}|localhost", host, re.IGNORECASE):
        suffix = host.rsplit(".", 1)[-1].lower() if "." in host else ""
        if suffix in _COMMON_FILEISH_SUFFIXES:
            return ""
        scheme = "https"
    else:
        scheme = "http"
    return urllib.parse.urlunparse(
        (
            scheme,
            str(probe.netloc or "").strip(),
            str(probe.path or "").strip(),
            "",
            str(probe.query or "").strip(),
            str(probe.fragment or "").strip(),
        )
    )


def _extract_browser_allowed_domains(url: str) -> list[str]:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    host = str(parsed.hostname or "").strip().lower()
    if not host:
        return []
    if host.startswith("www."):
        host = host[4:]
    return [host]


def _extract_browser_selector_hint(text: str) -> str:
    match = _BROWSER_SELECTOR_HINT_RE.search(str(text or ""))
    return str(match.group(1) or "").strip() if match else ""


def _extract_browser_input_hint(text: str) -> tuple[str, str]:
    match = _BROWSER_TYPE_SELECTOR_RE.search(str(text or ""))
    if match:
        raw_value = str(match.group("value") or "").strip()
        if (raw_value.startswith('"') and raw_value.endswith('"')) or (raw_value.startswith("'") and raw_value.endswith("'")):
            raw_value = raw_value[1:-1]
        return raw_value.strip(), str(match.group("selector") or "").strip()

    match = _BROWSER_TYPE_HUMAN_SELECTOR_RE.search(str(text or ""))
    if not match:
        return "", ""
    raw_value = str(match.group("value") or "").strip()
    if (raw_value.startswith('"') and raw_value.endswith('"')) or (raw_value.startswith("'") and raw_value.endswith("'")):
        raw_value = raw_value[1:-1]
    label = str(match.group("label") or "").strip().lower()
    if not label:
        return raw_value.strip(), ""
    return raw_value.strip(), f"[name='{label}']"


def _infer_browser_goal_kind(text: str) -> str:
    raw = str(text or "")
    if _BROWSER_DOWNLOAD_HINT_RE.search(raw):
        return "download"
    if _BROWSER_FORM_HINT_RE.search(raw):
        return "form_fill"
    if _BROWSER_EXTRACT_HINT_RE.search(raw):
        return "extract"
    return "navigate"


def _extract_browser_download_dir(text: str) -> str:
    match = _BROWSER_DOWNLOAD_DIR_RE.search(str(text or ""))
    if not match:
        return ""
    raw_value = str(match.group("path") or "").strip()
    if (raw_value.startswith('"') and raw_value.endswith('"')) or (raw_value.startswith("'") and raw_value.endswith("'")):
        raw_value = raw_value[1:-1]
    raw_value = raw_value.strip().replace("\\", "/")
    if not raw_value:
        return ""
    raw_value = raw_value.rstrip(".,;:!?")
    if not raw_value:
        return ""
    if raw_value.startswith("/") or re.match(r"^[A-Za-z]:", raw_value):
        return ""
    return raw_value


def _extract_browser_download_hint(text: str) -> str:
    match = _BROWSER_DOWNLOAD_TARGET_RE.search(str(text or ""))
    if not match:
        return ""
    target = str(match.group("target") or "").strip().strip(".,;:!?")
    if not target:
        return ""
    if len(target) >= 2 and target[0] == target[-1] and target[0] in {"'", '"', "`"}:
        target = target[1:-1].strip()
    target = re.sub(r"(?i)\b(?:please|now)\b$", "", target).strip(" ,.;:!?")
    target_tokens = [token for token in re.findall(r"[a-z0-9]+", target.lower()) if token]
    if len(target_tokens) == 1 and target_tokens[0] in _BROWSER_DOWNLOAD_PRONOUNS:
        return ""
    return target


def _with_browser_selector_hint(route: RouteDecision, selector_hint: str) -> RouteDecision:
    if not selector_hint or str((route or {}).get("decision") or "").strip().upper() != "TASK":
        return route
    card = dict((route or {}).get("card") or {})
    stages = card.get("stages") or []
    if not stages or not isinstance(stages[0], dict):
        return route
    stage = dict(stages[0])
    if str(stage.get("stage_type") or "").strip().upper() != "COMPUTER_USE":
        return route
    meta = dict(stage.get("computer_use") or {})
    meta["selector_hint"] = selector_hint
    stage["computer_use"] = meta
    updated_stages = list(stages)
    updated_stages[0] = stage
    context = [str(item).strip() for item in (card.get("context") or []) if str(item).strip()]
    selector_line = f"Selector hint: {selector_hint}"
    if selector_line not in context:
        context.append(selector_line)
    card["context"] = context
    card["stages"] = updated_stages
    updated = dict(route)
    updated["card"] = card
    return updated


def _with_browser_requested_topic(route: RouteDecision, requested_topic: str) -> RouteDecision:
    clean_topic = _clean_browser_requested_topic(requested_topic)
    if not clean_topic or str((route or {}).get("decision") or "").strip().upper() != "TASK":
        return route
    card = dict((route or {}).get("card") or {})
    stages = card.get("stages") or []
    if not stages or not isinstance(stages[0], dict):
        return route
    stage = dict(stages[0])
    if str(stage.get("stage_type") or "").strip().upper() != "COMPUTER_USE":
        return route
    meta = dict(stage.get("computer_use") or {})
    meta["requested_topic"] = clean_topic
    stage["computer_use"] = meta
    updated_stages = list(stages)
    updated_stages[0] = stage
    context = [str(item).strip() for item in (card.get("context") or []) if str(item).strip()]
    topic_line = f"Requested topic: {clean_topic}"
    if topic_line not in context:
        context.append(topic_line)
    card["context"] = context
    card["stages"] = updated_stages
    updated = dict(route)
    updated["card"] = card
    return updated


def _with_browser_avoid_heading(route: RouteDecision, avoid_heading: str) -> RouteDecision:
    clean_heading = str(avoid_heading or "").strip()
    if not clean_heading or str((route or {}).get("decision") or "").strip().upper() != "TASK":
        return route
    card = dict((route or {}).get("card") or {})
    stages = card.get("stages") or []
    if not stages or not isinstance(stages[0], dict):
        return route
    stage = dict(stages[0])
    if str(stage.get("stage_type") or "").strip().upper() != "COMPUTER_USE":
        return route
    meta = dict(stage.get("computer_use") or {})
    meta["avoid_heading"] = clean_heading
    stage["computer_use"] = meta
    updated_stages = list(stages)
    updated_stages[0] = stage
    context = [str(item).strip() for item in (card.get("context") or []) if str(item).strip()]
    heading_line = f"Avoid repeating the section under heading: {clean_heading}"
    if heading_line not in context:
        context.append(heading_line)
    card["context"] = context
    card["stages"] = updated_stages
    updated = dict(route)
    updated["card"] = card
    return updated


def _join_browser_steps(parts: list[str]) -> str:
    cleaned = [str(part or "").strip() for part in parts if str(part or "").strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"


def _runtime_context_implies_browser_task(runtime: dict[str, str]) -> bool:
    if str(runtime.get("previous_route") or "").strip().upper() != "TASK":
        return False
    combined = " ".join(
        [
            str(runtime.get("task_goal") or ""),
            str(runtime.get("runtime_note") or ""),
            str(runtime.get("previous_user_request") or ""),
        ]
    ).lower()
    return "browser" in combined and bool(extract_browser_url(combined))


def _extract_browser_url_from_runtime(runtime: dict[str, str]) -> str:
    for candidate in (
        str(runtime.get("task_goal") or "").strip(),
        str(runtime.get("runtime_note") or "").strip(),
        str(runtime.get("previous_user_request") or "").strip(),
    ):
        url = extract_browser_url(candidate)
        if url:
            return url
    return ""


def _extract_browser_context_from_recent_history(
    recent_history: Sequence[dict[str, Any]],
    *,
    current_text: str,
) -> tuple[str, str]:
    for message in reversed(list(recent_history or [])[-8:]):
        if str(message.get("role") or "").strip().lower() == "system" or bool(message.get("hidden")):
            continue
        content = str(message.get("content") or "").strip()
        if not content or content == current_text:
            continue
        url = extract_browser_url(content)
        if not url or not _message_implies_browser_page_context(content):
            continue
        return url, _extract_browser_requested_topic(content)
    return "", ""


def _message_implies_browser_page_context(text: str) -> bool:
    raw = str(text or "")
    if not extract_browser_url(raw):
        return False
    return bool(
        _BROWSER_REQUEST_HINT_RE.search(raw)
        or _BROWSER_EXTRACT_HINT_RE.search(raw)
        or _BROWSER_CONTEXT_HISTORY_HINT_RE.search(raw)
    )


def _looks_like_browser_detail_followup(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    return bool(_BROWSER_DETAIL_FOLLOWUP_RE.search(raw))


def _looks_like_browser_broad_followup(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if re.fullmatch(r"(?is)(?:what\s+)?(?:else|more)(?:\s+is\s+there)?[.!?]*", raw):
        return True
    if re.fullmatch(r"(?is)(?:anything|something)\s+else[.!?]*", raw):
        return True
    if re.fullmatch(r"(?is)(?:general\s+)?(?:info(?:rmation)?|overview|summary|details?)[.!?]*", raw):
        return True
    tokens = re.findall(r"[a-z0-9']+", raw.lower())
    if len(tokens) <= 6 and _BROWSER_MORE_OR_ELSE_RE.search(raw):
        return True
    return False


def _looks_like_browser_topic_reply(text: str, recent_history: Sequence[dict[str, Any]]) -> bool:
    raw = str(text or "").strip()
    if not raw or raw.endswith("?"):
        return False
    tokens = re.findall(r"[a-z0-9']+", raw.lower())
    if not tokens or len(tokens) > _SHORT_BROWSER_TOPIC_REPLY_MAX_TOKENS:
        return False
    if _looks_like_browser_detail_followup(raw):
        return False
    assistant_text = _latest_assistant_message(recent_history)
    if re.fullmatch(r"(?is)\s*(?:yes|no|maybe|sure|okay|ok|thanks|thank you)\s*[.!?]*", raw):
        return False
    if assistant_text and _BROWSER_INFO_PROMPT_RE.search(assistant_text):
        return True
    return len(tokens) >= 2


def _latest_assistant_message(recent_history: Sequence[dict[str, Any]]) -> str:
    for message in reversed(list(recent_history or [])):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip().lower() != "assistant":
            continue
        content = str(message.get("content") or "").strip()
        if content and content.lower() != "thinking...":
            return content
    return ""


def _extract_recent_browser_reply_heading(recent_history: Sequence[dict[str, Any]]) -> str:
    assistant_text = _latest_assistant_message(recent_history)
    if not assistant_text:
        return ""
    for pattern in (_BROWSER_REPLY_HEADING_RE, _BROWSER_REPLY_MAIN_HEADING_RE):
        match = pattern.search(assistant_text)
        if match:
            return str(match.group("heading") or "").strip()
    return ""


def _extract_browser_requested_topic(text: str) -> str:
    match = _BROWSER_TOPIC_RE.search(str(text or ""))
    if not match:
        return ""
    return _clean_browser_requested_topic(match.group("topic") or match.group("topic_alt"))


def _clean_browser_requested_topic(text: str) -> str:
    topic = str(text or "").strip().strip(".,;:!?")
    if not topic:
        return ""
    if len(topic) >= 2 and topic[0] == topic[-1] and topic[0] in {"'", '"', "`"}:
        topic = topic[1:-1].strip()
    topic = re.sub(r"(?i)\b(?:for you|to me|for me|please|now)\b$", "", topic).strip(" ,.;:!?")
    return topic


def _classify_browser_followup_intent(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if _BROWSER_DOWNLOAD_HINT_RE.search(raw):
        return "download"
    if re.search(r"(?i)\b(?:page\s+)?title\b", raw):
        return "title"
    if _BROWSER_HEADING_HINT_RE.search(raw):
        return "heading"
    if _BROWSER_CLICK_HUMAN_HINT_RE.search(raw):
        return "navigate"
    selector_hint = _extract_browser_selector_hint(raw)
    if selector_hint:
        return "extract"
    if _BROWSER_EXTRACT_HINT_RE.search(raw) or _BROWSER_READLIKE_HINT_RE.search(raw):
        return "extract"
    tokens = re.findall(r"[a-z0-9']+", raw.lower())
    if len(tokens) <= 4 and _BROWSER_MORE_OR_ELSE_RE.search(raw):
        return "extract"
    if (
        _BROWSER_WH_QUERY_RE.match(raw)
        and len(tokens) <= 10
        and (_BROWSER_MORE_OR_ELSE_RE.search(raw) or _BROWSER_PAGE_REFERENCE_RE.search(raw))
    ):
        return "extract"
    return ""
