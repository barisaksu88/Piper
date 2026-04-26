from __future__ import annotations

from dataclasses import dataclass

SEARCH_RESULT_PREFIX = "Background search complete for '"
SEARCH_FAILURE_PREFIX = "Background search failed for '"
SEARCH_REPORTER_INSTRUCTION = "The web search is complete. Summarize the findings for the user now."
SEARCH_FAILURE_REPORTER_INSTRUCTION = (
    "The web search failed. Tell the user what went wrong without claiming retrieved results."
)
SEARCH_TOOL_ERROR_PREFIX = "Search Error:"


@dataclass(frozen=True)
class BackgroundSearchPayload:
    query: str = "Unknown Query"
    data: str = ""
    failed: bool = False


def is_search_error_result(value: object) -> bool:
    return str(value or "").lstrip().casefold().startswith(SEARCH_TOOL_ERROR_PREFIX.casefold())


def normalize_search_error(value: object) -> str:
    text = str(value or "").strip()
    if is_search_error_result(text):
        _, _, tail = text.partition(":")
        return tail.strip() or text
    return text


def is_background_search_payload(content: object) -> bool:
    text = str(content or "")
    return text.startswith(SEARCH_RESULT_PREFIX) or text.startswith(SEARCH_FAILURE_PREFIX)


def is_search_reporter_instruction(content: object) -> bool:
    text = str(content or "").strip()
    return text in {SEARCH_REPORTER_INSTRUCTION, SEARCH_FAILURE_REPORTER_INSTRUCTION}


def build_background_search_content(query: object, data: object, *, failed: bool = False) -> str:
    prefix = SEARCH_FAILURE_PREFIX if failed else SEARCH_RESULT_PREFIX
    label = "Error" if failed else "Data"
    return f"{prefix}{str(query or '').strip()}'. {label}:\n{str(data or '').strip()}"


def parse_background_search_content(content: object) -> BackgroundSearchPayload:
    raw = str(content or "")
    failed = raw.startswith(SEARCH_FAILURE_PREFIX)
    prefix = SEARCH_FAILURE_PREFIX if failed else SEARCH_RESULT_PREFIX
    query = "Unknown Query"
    data = raw

    if raw.startswith(prefix):
        try:
            parts = raw.split("'", 2)
            if len(parts) >= 2 and parts[1].strip():
                query = parts[1].strip()
        except Exception:
            query = "Unknown Query"

    marker = "Error:\n" if failed else "Data:\n"
    if marker in raw:
        data = raw.split(marker, 1)[1].strip()
    elif "Data:\n" in raw:
        data = raw.split("Data:\n", 1)[1].strip()
    elif "Error:\n" in raw:
        data = raw.split("Error:\n", 1)[1].strip()

    if is_search_error_result(data):
        failed = True

    return BackgroundSearchPayload(query=query, data=data, failed=failed)
