"""Build a grounded answer from extracted evidence."""

from __future__ import annotations

from typing import List

from core.search.contracts import SearchAnswerEvidence, SourcePassage


def build_answer(evidence: SearchAnswerEvidence) -> SearchAnswerEvidence:
    """Populate evidence.answer_text and evidence.verdict from chosen sources.

    Rules:
    - If the top chosen passage has a strong relevance score (>= 1.0), verdict is "verified".
    - If there are some relevant passages but none are strong, verdict is "partial".
    - If no passages were found, verdict is "not_verified" and answer says so.
    """
    passages = evidence.chosen_sources
    if not passages:
        evidence.verdict = "not_verified"
        evidence.answer_text = (
            "The search did not return sufficient evidence to answer this query."
        )
        return evidence

    # Determine verdict
    top_score = passages[0].relevance_score if passages else 0.0
    if top_score >= 1.0:
        evidence.verdict = "verified"
    elif top_score >= 0.3:
        evidence.verdict = "partial"
    else:
        evidence.verdict = "not_verified"

    # Build answer text from passages
    lines: List[str] = []
    if evidence.verdict == "verified":
        lines.append("The search found strong evidence for the answer.")
    elif evidence.verdict == "partial":
        lines.append("The search found partial evidence; the answer may be incomplete.")
    else:
        lines.append("The search did not find reliable evidence to confirm the answer.")

    lines.append("")
    lines.append("Supporting evidence:")
    for i, p in enumerate(passages[:5], 1):
        lines.append(f"{i}. {p.text}")
        lines.append(f"   — {p.source_title}")

    evidence.answer_text = "\n".join(lines)
    return evidence
