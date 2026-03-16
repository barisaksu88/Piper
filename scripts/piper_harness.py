from __future__ import annotations

import argparse
import json
import sys

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CLI harness for Piper without the GUI.")
    parser.add_argument("--persist-turns", action="store_true")
    parser.add_argument("--enable-memory-learning", action="store_true")
    parser.add_argument("--live-data", action="store_true", help="Use the live data directory instead of an isolated copy.")
    parser.add_argument("--keep-data-copy", action="store_true", help="Preserve the isolated data copy after exit for inspection.")
    sub = parser.add_subparsers(dest="command")

    once_cmd = sub.add_parser("once", help="Run a single user turn.")
    once_cmd.add_argument("text", help="User input to send to Piper.")
    once_cmd.add_argument("--timeout", type=float, default=180.0)
    once_cmd.add_argument("--json", action="store_true", dest="as_json")

    shell_cmd = sub.add_parser("shell", help="Start an interactive harness shell.")
    shell_cmd.add_argument("--timeout", type=float, default=180.0)
    shell_cmd.add_argument("--json", action="store_true", dest="as_json")

    dump_cmd = sub.add_parser("dump", help="Dump harness state after boot.")
    dump_cmd.add_argument("--json", action="store_true", dest="as_json")

    return parser


def print_boot(boot, *, as_json: bool) -> None:
    if as_json:
        return
    mode = "isolated" if boot.isolated_data else "live"
    print(f"DATA_MODE: {mode}")
    print(f"DATA_DIR: {boot.data_dir}")


def print_turn(result, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.__dict__, indent=2, ensure_ascii=False))
        return

    print(f"USER: {result.user_text}")
    print(f"ASSISTANT: {result.assistant_text or '(no assistant reply)'}")
    if result.system_messages:
        print("SYSTEM:")
        for line in result.system_messages:
            print(f"  - {line}")
    if result.tts_utterances:
        print("TTS:")
        for utterance in result.tts_utterances:
            text = utterance.get("text") or ""
            voice = utterance.get("voice")
            speed = utterance.get("speed")
            print(f"  - voice={voice} speed={speed} text={text!r}")
    if result.images:
        print("IMAGES:")
        for image in result.images:
            print(f"  - {image}")
    print(f"STATUS: {result.status_history[-1] if result.status_history else '(none)'}")
    print(f"TIMED_OUT: {result.timed_out}")
    print(f"DURATION_S: {result.duration_s}")


def main() -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args()
    command = args.command or "shell"

    harness = PiperHarness(
        persist_turns=args.persist_turns,
        enable_memory_learning=args.enable_memory_learning,
        isolated_data=not args.live_data,
        keep_data_copy=args.keep_data_copy,
    )
    boot = harness.start()
    if not boot.ready:
        print(json.dumps(boot.__dict__, indent=2, ensure_ascii=False))
        harness.close()
        return 1

    print_boot(boot, as_json=getattr(args, "as_json", False))

    try:
        if command == "once":
            result = harness.send_text(args.text, timeout_s=args.timeout)
            print_turn(result, as_json=args.as_json)
            return 0

        if command == "dump":
            state = harness.dump_state()
            print(json.dumps(state, indent=2, ensure_ascii=False))
            return 0

        while True:
            try:
                user_text = input("piper> ").strip()
            except EOFError:
                print()
                break
            if not user_text:
                continue
            if user_text.lower() in {"/exit", "/quit", "exit", "quit"}:
                break
            result = harness.send_text(user_text, timeout_s=args.timeout)
            print_turn(result, as_json=args.as_json)
            print("-" * 60)
        return 0
    finally:
        harness.close()
        if args.keep_data_copy and harness.kept_data_dir and not getattr(args, "as_json", False):
            print(f"KEPT_DATA_DIR: {harness.kept_data_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
