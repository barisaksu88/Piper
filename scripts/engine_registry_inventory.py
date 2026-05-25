"""scripts/engine_registry_inventory.py

Backend architecture observability script for Piper's engine/registry system.

Reports the current state of:
- route interceptors
- feature hooks
- persona tail blocks

Usage:
    python scripts/engine_registry_inventory.py
    python scripts/engine_registry_inventory.py --json
    python scripts/engine_registry_inventory.py --json --output registry.json

Constraints:
- Does not boot Piper app.
- Does not start UI.
- Does not require LLM server.
- Does not load models.
- Does not require web server.
"""
from __future__ import annotations

import argparse
import inspect
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Ensure project root is on path for imports
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import the normal orchestrator registration path so side-effect registrations happen.
from core import orchestrator  # noqa: F401

# Also import prompt_context because it registers an on_pre_route hook during
# normal app boot (app.py imports it), but orchestrator.py does not.
from core import prompt_context  # noqa: F401

from core.engines.tail_block_registry import _TAIL_BLOCK_REGISTRY
from core.feature_hooks import _HOOKS
from core.routing.route_normalizer import _ROUTE_INTERCEPTOR_REGISTRY


@dataclass(frozen=True)
class RegistryEntry:
    registry_type: str
    index: int
    module: str
    function_name: str
    qualname: str
    signature: str = ""


@dataclass(frozen=True)
class RegistryReport:
    route_interceptors: list[RegistryEntry] = field(default_factory=list)
    feature_hooks: dict[str, list[RegistryEntry]] = field(default_factory=dict)
    tail_blocks: list[RegistryEntry] = field(default_factory=list)


def _build_signature(fn: Any) -> str:
    try:
        return str(inspect.signature(fn))
    except (ValueError, TypeError):
        return ""


def _inspect_route_interceptors() -> list[RegistryEntry]:
    entries: list[RegistryEntry] = []
    for idx, fn in enumerate(_ROUTE_INTERCEPTOR_REGISTRY):
        entries.append(
            RegistryEntry(
                registry_type="route_interceptor",
                index=idx,
                module=getattr(fn, "__module__", "<unknown>"),
                function_name=getattr(fn, "__name__", "<unknown>"),
                qualname=getattr(fn, "__qualname__", getattr(fn, "__name__", "<unknown>")),
                signature=_build_signature(fn),
            )
        )
    return entries


def _inspect_feature_hooks() -> dict[str, list[RegistryEntry]]:
    result: dict[str, list[RegistryEntry]] = {}
    for hook_type, hooks in sorted(_HOOKS.items()):
        entries: list[RegistryEntry] = []
        for idx, fn in enumerate(hooks):
            entries.append(
                RegistryEntry(
                    registry_type="feature_hook",
                    index=idx,
                    module=getattr(fn, "__module__", "<unknown>"),
                    function_name=getattr(fn, "__name__", "<unknown>"),
                    qualname=getattr(fn, "__qualname__", getattr(fn, "__name__", "<unknown>")),
                    signature=_build_signature(fn),
                )
            )
        result[hook_type] = entries
    return result


def _inspect_tail_blocks() -> list[RegistryEntry]:
    entries: list[RegistryEntry] = []
    for idx, fn in enumerate(_TAIL_BLOCK_REGISTRY):
        entries.append(
            RegistryEntry(
                registry_type="tail_block",
                index=idx,
                module=getattr(fn, "__module__", "<unknown>"),
                function_name=getattr(fn, "__name__", "<unknown>"),
                qualname=getattr(fn, "__qualname__", getattr(fn, "__name__", "<unknown>")),
                signature=_build_signature(fn),
            )
        )
    return entries


def build_report() -> RegistryReport:
    return RegistryReport(
        route_interceptors=_inspect_route_interceptors(),
        feature_hooks=_inspect_feature_hooks(),
        tail_blocks=_inspect_tail_blocks(),
    )


def _entry_to_dict(entry: RegistryEntry) -> dict[str, Any]:
    return {
        "registry_type": entry.registry_type,
        "index": entry.index,
        "module": entry.module,
        "function_name": entry.function_name,
        "qualname": entry.qualname,
        "signature": entry.signature,
    }


def report_to_dict(report: RegistryReport) -> dict[str, Any]:
    return {
        "route_interceptors": [_entry_to_dict(e) for e in report.route_interceptors],
        "feature_hooks": {
            hook_type: [_entry_to_dict(e) for e in entries]
            for hook_type, entries in report.feature_hooks.items()
        },
        "tail_blocks": [_entry_to_dict(e) for e in report.tail_blocks],
        "summary": {
            "route_interceptor_count": len(report.route_interceptors),
            "feature_hook_counts": {
                hook_type: len(entries) for hook_type, entries in report.feature_hooks.items()
            },
            "tail_block_count": len(report.tail_blocks),
        },
    }


def _render_text(report: RegistryReport) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("Piper Engine Registry Inventory")
    lines.append("=" * 60)
    lines.append("")

    lines.append(f"Route Interceptors ({len(report.route_interceptors)})")
    lines.append("-" * 40)
    for e in report.route_interceptors:
        sig = f" {e.signature}" if e.signature else ""
        lines.append(f"  [{e.index}] {e.module}.{e.qualname}{sig}")
    lines.append("")

    lines.append("Feature Hooks")
    lines.append("-" * 40)
    for hook_type, entries in sorted(report.feature_hooks.items()):
        lines.append(f"  {hook_type} ({len(entries)})")
        for e in entries:
            sig = f" {e.signature}" if e.signature else ""
            lines.append(f"    [{e.index}] {e.module}.{e.qualname}{sig}")
    lines.append("")

    lines.append(f"Tail Blocks ({len(report.tail_blocks)})")
    lines.append("-" * 40)
    for e in report.tail_blocks:
        sig = f" {e.signature}" if e.signature else ""
        lines.append(f"  [{e.index}] {e.module}.{e.qualname}{sig}")
    lines.append("")

    lines.append("Summary")
    lines.append("-" * 40)
    lines.append(f"  route_interceptors: {len(report.route_interceptors)}")
    for hook_type, count in sorted(
        {k: len(v) for k, v in report.feature_hooks.items()}.items()
    ):
        lines.append(f"  feature_hooks.{hook_type}: {count}")
    lines.append(f"  tail_blocks: {len(report.tail_blocks)}")
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Piper engine registry inventory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--output", type=str, default="", help="Optional output file path")
    args = parser.parse_args(argv)

    report = build_report()

    if args.json:
        output = json.dumps(report_to_dict(report), indent=2, ensure_ascii=False)
    else:
        output = _render_text(report)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Inventory written to: {args.output}")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
