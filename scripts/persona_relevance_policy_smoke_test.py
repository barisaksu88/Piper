from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.contracts import PromptContext  # noqa: E402
from core.prompt_builder import PromptBuilder  # noqa: E402


def main() -> int:
    prompt = PromptBuilder.build_persona_prompt(
        PromptContext(
            instructions="You are Piper.",
            world_state="[WORLD STATE]\nFuture Plans: house in Bostanci",
            situational_state="[SITUATIONAL STATE]\nDentist appointment sentiment: hesitant",
            operational_state="[OPERATIONAL STATE]\nDentist appointment on 2026-03-24",
            env_block="[ENVIRONMENT]\nClear sky",
            brain_hits=[{"text": "likes cheap coffee", "metadata": {"date": "Mar 13, 2026"}}],
        )
    )
    prompt_with_existing_block = PromptBuilder.build_persona_prompt(
        PromptContext(
            instructions=(
                "You are Piper.\n\n"
                "[RELEVANCE DISCIPLINE]\n"
                "Use only the most directly relevant contextual fact by default.\n"
            ),
            world_state="[WORLD STATE]\nFuture Plans: house in Bostanci",
            situational_state="[SITUATIONAL STATE]\nDentist appointment sentiment: hesitant",
            operational_state="[OPERATIONAL STATE]\nDentist appointment on 2026-03-24",
            env_block="[ENVIRONMENT]\nClear sky",
            brain_hits=[{"text": "likes cheap coffee", "metadata": {"date": "Mar 13, 2026"}}],
        )
    )
    prompt_with_markdown_heading = PromptBuilder.build_persona_prompt(
        PromptContext(
            instructions=(
                "You are Piper.\n\n"
                "## RELEVANCE DISCIPLINE\n"
                "Use only the most directly relevant contextual fact by default.\n"
            ),
            world_state="[WORLD STATE]\nFuture Plans: house in Bostanci",
            situational_state="[SITUATIONAL STATE]\nDentist appointment sentiment: hesitant",
            operational_state="[OPERATIONAL STATE]\nDentist appointment on 2026-03-24",
            env_block="[ENVIRONMENT]\nClear sky",
            brain_hits=[{"text": "likes cheap coffee", "metadata": {"date": "Mar 13, 2026"}}],
        )
    )
    success = (
        "[RELEVANCE DISCIPLINE]" in prompt
        and prompt.count("[RELEVANCE DISCIPLINE]") == 1
        and "Use only the most directly relevant contextual fact by default." in prompt
        and "Do not pile multiple unrelated profile facts, memories, and future plans into one reply." in prompt
        and "Do not exaggerate recalled facts just to make the tone sharper." in prompt
        and prompt_with_existing_block.count("[RELEVANCE DISCIPLINE]") == 1
        and prompt_with_markdown_heading.count("[RELEVANCE DISCIPLINE]") == 0
        and prompt_with_markdown_heading.count("## RELEVANCE DISCIPLINE") == 1
    )
    print(
        json.dumps(
            {
                "success": bool(success),
                "has_relevance_block": "[RELEVANCE DISCIPLINE]" in prompt,
                "relevance_block_count": prompt.count("[RELEVANCE DISCIPLINE]"),
                "existing_block_count": prompt_with_existing_block.count("[RELEVANCE DISCIPLINE]"),
                "markdown_heading_count": prompt_with_markdown_heading.count("## RELEVANCE DISCIPLINE"),
                "markdown_fallback_block_count": prompt_with_markdown_heading.count("[RELEVANCE DISCIPLINE]"),
                "prompt_excerpt": prompt,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
