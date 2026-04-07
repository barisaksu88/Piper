from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.routing.route_normalizer import normalize_route_decision


@dataclass(frozen=True)
class ComputerUseRouteSmokeReport:
    success: bool
    decision: str
    stage_type: str
    allowed_tools: list[str]
    start_url: str
    selector_hint: str
    goal_kind: str
    compound_success: bool
    compound_download_dir: str
    compound_download_hint: str
    compound_stage_goal: str
    bare_domain_success: bool
    bare_domain_start_url: str
    path_domain_success: bool
    path_domain_start_url: str
    path_domain_allowed_domains: list[str]
    alt_title_success: bool
    alt_title_start_url: str
    alt_heading_success: bool
    alt_heading_selector_hint: str
    heading_success: bool
    heading_selector_hint: str
    heading_stage_goal: str
    contextual_followup_success: bool
    contextual_followup_start_url: str
    contextual_followup_requested_topic: str
    contextual_followup_stage_goal: str
    download_followup_success: bool
    download_followup_start_url: str
    download_followup_hint: str
    download_followup_stage_goal: str
    download_only_success: bool
    download_only_require_extract: bool
    download_only_stage_goal: str


def run_smoke() -> ComputerUseRouteSmokeReport:
    url = (ROOT_DIR / "scripts" / "fixtures" / "computer_use" / "index.html").resolve().as_uri()
    user_msg = f"Open {url} in the browser and extract the text from #status."
    normalized = normalize_route_decision({"decision": "CHAT"}, user_msg, [])
    decision = str((normalized or {}).get("decision") or "")
    card = (normalized or {}).get("card") or {}
    stages = card.get("stages") or []
    stage = stages[0] if stages and isinstance(stages[0], dict) else {}
    computer_use = stage.get("computer_use") or {}
    stage_type = str(stage.get("stage_type") or "")
    allowed_tools = list(stage.get("allowed_tools") or [])
    start_url = str(computer_use.get("start_url") or "")
    selector_hint = str(computer_use.get("selector_hint") or "")
    goal_kind = str(computer_use.get("goal_kind") or "")
    simple_success = (
        decision == "TASK"
        and stage_type == "COMPUTER_USE"
        and allowed_tools == ["BROWSER_OP"]
        and start_url == url
        and selector_hint == "#status"
        and goal_kind == "extract"
    )
    compound_msg = f"Open {url} in the browser, tell me the status text, then download the report into browser_downloads."
    compound = normalize_route_decision({"decision": "CHAT"}, compound_msg, [])
    compound_stage = (((compound or {}).get("card") or {}).get("stages") or [{}])[0]
    compound_meta = compound_stage.get("computer_use") or {}
    compound_stage_goal = str(compound_stage.get("stage_goal") or "")
    compound_success_condition = str(compound_stage.get("success_condition") or "")
    compound_download_dir = str(compound_meta.get("download_dir") or "")
    compound_download_hint = str(compound_meta.get("download_hint") or "")
    compound_success = (
        str((compound or {}).get("decision") or "") == "TASK"
        and str(compound_stage.get("stage_type") or "") == "COMPUTER_USE"
        and compound_download_dir == "browser_downloads"
        and compound_download_hint == "report"
        and "status text" in compound_stage_goal.lower()
        and "download" in compound_stage_goal.lower()
        and "browser_downloads" in compound_success_condition
    )
    bare_domain = normalize_route_decision({"decision": "CHAT"}, "Open example.com in the browser and tell me the page title.", [])
    bare_stage = (((bare_domain or {}).get("card") or {}).get("stages") or [{}])[0]
    bare_meta = bare_stage.get("computer_use") or {}
    bare_domain_start_url = str(bare_meta.get("start_url") or "")
    bare_domain_success = (
        str((bare_domain or {}).get("decision") or "") == "TASK"
        and str(bare_stage.get("stage_type") or "") == "COMPUTER_USE"
        and bare_domain_start_url == "https://example.com"
        and list(bare_meta.get("allowed_domains") or []) == ["example.com"]
    )
    path_domain = normalize_route_decision(
        {"decision": "CHAT"},
        "Open iana.org/domains/reserved in the browser and tell me the page title.",
        [],
    )
    path_stage = (((path_domain or {}).get("card") or {}).get("stages") or [{}])[0]
    path_meta = path_stage.get("computer_use") or {}
    path_domain_start_url = str(path_meta.get("start_url") or "")
    path_domain_allowed_domains = list(path_meta.get("allowed_domains") or [])
    path_domain_success = (
        str((path_domain or {}).get("decision") or "") == "TASK"
        and str(path_stage.get("stage_type") or "") == "COMPUTER_USE"
        and path_domain_start_url == "https://iana.org/domains/reserved"
        and path_domain_allowed_domains == ["iana.org"]
    )
    alt_title = normalize_route_decision(
        {"decision": "CHAT"},
        "What's the title of example.com?",
        [],
    )
    alt_title_stage = (((alt_title or {}).get("card") or {}).get("stages") or [{}])[0]
    alt_title_meta = alt_title_stage.get("computer_use") or {}
    alt_title_start_url = str(alt_title_meta.get("start_url") or "")
    alt_title_success = (
        str((alt_title or {}).get("decision") or "") == "TASK"
        and str(alt_title_stage.get("stage_type") or "") == "COMPUTER_USE"
        and alt_title_start_url == "https://example.com"
        and bool(alt_title_meta.get("report_title"))
    )
    alt_heading = normalize_route_decision(
        {"decision": "CHAT"},
        "What's the main heading on example.com?",
        [],
    )
    alt_heading_stage = (((alt_heading or {}).get("card") or {}).get("stages") or [{}])[0]
    alt_heading_meta = alt_heading_stage.get("computer_use") or {}
    alt_heading_selector_hint = str(alt_heading_meta.get("selector_hint") or "")
    alt_heading_success = (
        str((alt_heading or {}).get("decision") or "") == "TASK"
        and str(alt_heading_stage.get("stage_type") or "") == "COMPUTER_USE"
        and alt_heading_selector_hint == "h1"
    )
    heading = normalize_route_decision({"decision": "CHAT"}, "Open example.com in the browser and tell me the main heading.", [])
    heading_stage = (((heading or {}).get("card") or {}).get("stages") or [{}])[0]
    heading_meta = heading_stage.get("computer_use") or {}
    heading_selector_hint = str(heading_meta.get("selector_hint") or "")
    heading_stage_goal = str(heading_stage.get("stage_goal") or "")
    heading_success = (
        str((heading or {}).get("decision") or "") == "TASK"
        and str(heading_stage.get("stage_type") or "") == "COMPUTER_USE"
        and heading_selector_hint == "h1"
        and "page heading" in heading_stage_goal.lower()
    )
    contextual_history = [
        {
            "role": "assistant",
            "content": 'The page title at https://www.python.org/about/license/ is "Welcome to Python.org".',
        },
        {
            "role": "assistant",
            "content": (
                "The Python license documentation on docs.python.org/3/license.html primarily details "
                "license history. If you would like me to retrieve the specific warranty disclaimers "
                "and liability limitations, let me know."
            ),
        },
    ]
    contextual_router_output = {
        "decision": "TASK",
        "card": {
            "goal": "Retrieve specific details from the Python license documentation",
            "stages": [
                {
                    "stage_goal": (
                        "Navigate to the Python license documentation page and extract details "
                        "regarding warranty disclaimers and liability limitations"
                    ),
                    "stage_type": "FILE_WORK",
                    "success_condition": (
                        "The specific text or sections related to warranty disclaimers and liability "
                        "limitations are extracted from the page."
                    ),
                }
            ],
        },
    }
    contextual_followup = normalize_route_decision(
        contextual_router_output,
        "retrieve those details for me",
        contextual_history,
    )
    contextual_stage = (((contextual_followup or {}).get("card") or {}).get("stages") or [{}])[0]
    contextual_meta = contextual_stage.get("computer_use") or {}
    contextual_followup_start_url = str(contextual_meta.get("start_url") or "")
    contextual_followup_requested_topic = str(contextual_meta.get("requested_topic") or "")
    contextual_followup_stage_goal = str(contextual_stage.get("stage_goal") or "")
    contextual_followup_success = (
        str((contextual_followup or {}).get("decision") or "") == "TASK"
        and str(contextual_stage.get("stage_type") or "") == "COMPUTER_USE"
        and contextual_followup_start_url == "https://docs.python.org/3/license.html"
        and contextual_followup_requested_topic == "warranty disclaimers and liability limitations"
        and "requested information about 'warranty disclaimers and liability limitations'" in contextual_followup_stage_goal.lower()
    )
    download_followup_history = [
        {
            "role": "system",
            "content": (
                "[LATEST_RUNTIME_CONTEXT]\n"
                "Previous route: TASK\n"
                "Previous user request: Open http://127.0.0.1:9000/download_hub.html in the browser and tell me the page title.\n"
                "Task goal: Use the browser to inspect the current page.\n"
                "Execution status: SUCCESS\n"
                "Runtime note: The page title at http://127.0.0.1:9000/download_hub.html is \"Download Hub Fixture\".\n"
                "Use this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict."
            ),
        },
        {
            "role": "assistant",
            "content": 'The page title at http://127.0.0.1:9000/download_hub.html is "Download Hub Fixture".',
        },
    ]
    download_followup = normalize_route_decision(
        {"decision": "CHAT"},
        "download the quarterly report into browser_downloads",
        download_followup_history,
    )
    download_followup_stage = (((download_followup or {}).get("card") or {}).get("stages") or [{}])[0]
    download_followup_meta = download_followup_stage.get("computer_use") or {}
    download_followup_start_url = str(download_followup_meta.get("start_url") or "")
    download_followup_hint = str(download_followup_meta.get("download_hint") or "")
    download_followup_stage_goal = str(download_followup_stage.get("stage_goal") or "")
    download_followup_success = (
        str((download_followup or {}).get("decision") or "") == "TASK"
        and str(download_followup_stage.get("stage_type") or "") == "COMPUTER_USE"
        and download_followup_start_url == "http://127.0.0.1:9000/download_hub.html"
        and download_followup_hint == "quarterly report"
        and "download the requested artifact matching 'quarterly report'" in download_followup_stage_goal.lower()
    )
    download_only = normalize_route_decision(
        {"decision": "CHAT"},
        "Open https://www.rfc-editor.org/rfc/rfc2606.html in the browser and download the text version into browser_downloads_real.",
        [],
    )
    download_only_stage = (((download_only or {}).get("card") or {}).get("stages") or [{}])[0]
    download_only_meta = download_only_stage.get("computer_use") or {}
    download_only_require_extract = bool(download_only_meta.get("require_extract"))
    download_only_stage_goal = str(download_only_stage.get("stage_goal") or "")
    download_only_success = (
        str((download_only or {}).get("decision") or "") == "TASK"
        and str(download_only_stage.get("stage_type") or "") == "COMPUTER_USE"
        and str(download_only_meta.get("goal_kind") or "") == "download"
        and str(download_only_meta.get("download_hint") or "") == "text version"
        and not download_only_require_extract
        and "requested on-page information" not in download_only_stage_goal.lower()
    )
    success = bool(
        simple_success
        and compound_success
        and bare_domain_success
        and path_domain_success
        and alt_title_success
        and alt_heading_success
        and heading_success
        and contextual_followup_success
        and download_followup_success
        and download_only_success
    )
    return ComputerUseRouteSmokeReport(
        success=bool(success),
        decision=decision,
        stage_type=stage_type,
        allowed_tools=allowed_tools,
        start_url=start_url,
        selector_hint=selector_hint,
        goal_kind=goal_kind,
        compound_success=bool(compound_success),
        compound_download_dir=compound_download_dir,
        compound_download_hint=compound_download_hint,
        compound_stage_goal=compound_stage_goal,
        bare_domain_success=bool(bare_domain_success),
        bare_domain_start_url=bare_domain_start_url,
        path_domain_success=bool(path_domain_success),
        path_domain_start_url=path_domain_start_url,
        path_domain_allowed_domains=path_domain_allowed_domains,
        alt_title_success=bool(alt_title_success),
        alt_title_start_url=alt_title_start_url,
        alt_heading_success=bool(alt_heading_success),
        alt_heading_selector_hint=alt_heading_selector_hint,
        heading_success=bool(heading_success),
        heading_selector_hint=heading_selector_hint,
        heading_stage_goal=heading_stage_goal,
        contextual_followup_success=bool(contextual_followup_success),
        contextual_followup_start_url=contextual_followup_start_url,
        contextual_followup_requested_topic=contextual_followup_requested_topic,
        contextual_followup_stage_goal=contextual_followup_stage_goal,
        download_followup_success=bool(download_followup_success),
        download_followup_start_url=download_followup_start_url,
        download_followup_hint=download_followup_hint,
        download_followup_stage_goal=download_followup_stage_goal,
        download_only_success=bool(download_only_success),
        download_only_require_extract=bool(download_only_require_extract),
        download_only_stage_goal=download_only_stage_goal,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test explicit browser-request route normalization.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke()
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report.success}")
        print(f"DECISION: {report.decision}")
        print(f"STAGE_TYPE: {report.stage_type}")
        print(f"ALLOWED_TOOLS: {report.allowed_tools}")
        print(f"START_URL: {report.start_url}")
        print(f"SELECTOR_HINT: {report.selector_hint}")
        print(f"GOAL_KIND: {report.goal_kind}")
        print(f"COMPOUND_SUCCESS: {report.compound_success}")
        print(f"COMPOUND_DOWNLOAD_DIR: {report.compound_download_dir}")
        print(f"COMPOUND_DOWNLOAD_HINT: {report.compound_download_hint}")
        print(f"COMPOUND_STAGE_GOAL: {report.compound_stage_goal}")
        print(f"BARE_DOMAIN_SUCCESS: {report.bare_domain_success}")
        print(f"BARE_DOMAIN_START_URL: {report.bare_domain_start_url}")
        print(f"PATH_DOMAIN_SUCCESS: {report.path_domain_success}")
        print(f"PATH_DOMAIN_START_URL: {report.path_domain_start_url}")
        print(f"PATH_DOMAIN_ALLOWED_DOMAINS: {report.path_domain_allowed_domains}")
        print(f"ALT_TITLE_SUCCESS: {report.alt_title_success}")
        print(f"ALT_TITLE_START_URL: {report.alt_title_start_url}")
        print(f"ALT_HEADING_SUCCESS: {report.alt_heading_success}")
        print(f"ALT_HEADING_SELECTOR_HINT: {report.alt_heading_selector_hint}")
        print(f"HEADING_SUCCESS: {report.heading_success}")
        print(f"HEADING_SELECTOR_HINT: {report.heading_selector_hint}")
        print(f"HEADING_STAGE_GOAL: {report.heading_stage_goal}")
        print(f"CONTEXTUAL_FOLLOWUP_SUCCESS: {report.contextual_followup_success}")
        print(f"CONTEXTUAL_FOLLOWUP_START_URL: {report.contextual_followup_start_url}")
        print(f"CONTEXTUAL_FOLLOWUP_REQUESTED_TOPIC: {report.contextual_followup_requested_topic}")
        print(f"CONTEXTUAL_FOLLOWUP_STAGE_GOAL: {report.contextual_followup_stage_goal}")
        print(f"DOWNLOAD_FOLLOWUP_SUCCESS: {report.download_followup_success}")
        print(f"DOWNLOAD_FOLLOWUP_START_URL: {report.download_followup_start_url}")
        print(f"DOWNLOAD_FOLLOWUP_HINT: {report.download_followup_hint}")
        print(f"DOWNLOAD_FOLLOWUP_STAGE_GOAL: {report.download_followup_stage_goal}")
        print(f"DOWNLOAD_ONLY_SUCCESS: {report.download_only_success}")
        print(f"DOWNLOAD_ONLY_REQUIRE_EXTRACT: {report.download_only_require_extract}")
        print(f"DOWNLOAD_ONLY_STAGE_GOAL: {report.download_only_stage_goal}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
