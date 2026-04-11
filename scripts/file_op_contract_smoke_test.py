from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.file_ops import (  # noqa: E402
    extract_tag_payload_text,
    normalize_action,
    parse_normalized_payload,
    parse_normalized_tool_tag_payload,
    path_list_from_payload,
    primary_path_from_payload,
    source_paths_from_payload,
)


@dataclass(frozen=True)
class FileOpContractSmokeReport:
    action_aliases_ok: bool
    single_path_alias_ok: bool
    root_alias_ok: bool
    move_many_alias_items_ok: bool
    find_paths_query_alias_ok: bool
    inline_tag_parse_ok: bool
    block_tag_parse_ok: bool
    success: bool


def run_smoke() -> FileOpContractSmokeReport:
    write_payload = parse_normalized_payload('{"action":"delete_file","target":"notes/todo.txt"}')
    root_payload = parse_normalized_payload('{"action":"list_dir","directory":"docs"}')
    move_many_payload = parse_normalized_payload(
        '{"action":"move_many","items":[{"source":"a.txt","destination":"archive/a.txt"},{"from":"b.txt","to":"archive/b.txt"}]}'
    )
    find_payload = parse_normalized_payload('{"action":"find_file","name":"grocery_list.txt","folder":"test"}')

    inline_tag = '[FILE_OP: {"action":"read_files","files":["a.txt","b.txt"]}]'
    inline_payload = parse_normalized_tool_tag_payload(inline_tag)

    block_tag = '[FILE_OP] {"action":"rename_file","source":"draft.txt","destination":"done.txt"} [/FILE_OP]'
    block_payload = parse_normalized_tool_tag_payload(block_tag)

    action_aliases_ok = normalize_action("delete_file") == "delete_path" and write_payload.get("action") == "delete_path"
    single_path_alias_ok = primary_path_from_payload(write_payload) == "notes/todo.txt"
    root_alias_ok = str(root_payload.get("root") or "") == "docs" and root_payload.get("action") == "list_tree"
    move_many_alias_items_ok = source_paths_from_payload(move_many_payload) == ["a.txt", "b.txt"]
    find_paths_query_alias_ok = (
        find_payload.get("action") == "find_paths"
        and str(find_payload.get("query") or "") == "grocery_list.txt"
        and str(find_payload.get("root") or "") == "test"
    )
    inline_tag_parse_ok = (
        extract_tag_payload_text(inline_tag) == '{"action":"read_files","files":["a.txt","b.txt"]}'
        and inline_payload.get("action") == "read_many"
        and path_list_from_payload(inline_payload) == ["a.txt", "b.txt"]
    )
    block_tag_parse_ok = (
        block_payload.get("action") == "move_path"
        and source_paths_from_payload(block_payload) == ["draft.txt"]
    )

    success = all(
        [
            action_aliases_ok,
            single_path_alias_ok,
            root_alias_ok,
            move_many_alias_items_ok,
            find_paths_query_alias_ok,
            inline_tag_parse_ok,
            block_tag_parse_ok,
        ]
    )
    return FileOpContractSmokeReport(
        action_aliases_ok=action_aliases_ok,
        single_path_alias_ok=single_path_alias_ok,
        root_alias_ok=root_alias_ok,
        move_many_alias_items_ok=move_many_alias_items_ok,
        find_paths_query_alias_ok=find_paths_query_alias_ok,
        inline_tag_parse_ok=inline_tag_parse_ok,
        block_tag_parse_ok=block_tag_parse_ok,
        success=success,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test the shared FILE_OP contract normalization helpers.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke()
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report.success}")
        print(f"ACTION_ALIASES_OK: {report.action_aliases_ok}")
        print(f"SINGLE_PATH_ALIAS_OK: {report.single_path_alias_ok}")
        print(f"ROOT_ALIAS_OK: {report.root_alias_ok}")
        print(f"MOVE_MANY_ALIAS_ITEMS_OK: {report.move_many_alias_items_ok}")
        print(f"FIND_PATHS_QUERY_ALIAS_OK: {report.find_paths_query_alias_ok}")
        print(f"INLINE_TAG_PARSE_OK: {report.inline_tag_parse_ok}")
        print(f"BLOCK_TAG_PARSE_OK: {report.block_tag_parse_ok}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
