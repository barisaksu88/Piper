from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import PurePosixPath
import re


def normalize_file_reference_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def file_reference_tokens(text: str) -> list[str]:
    return [token for token in normalize_file_reference_text(text).split() if token]


def looks_like_file_reference_typo_close_match(target: str, candidate: str) -> bool:
    left = str(target or "").strip().lower()
    right = str(candidate or "").strip().lower()
    if not left or not right or left == right:
        return bool(left and right and left == right)
    if left[0] != right[0]:
        return False
    shared_prefix = 0
    for a, b in zip(left, right):
        if a != b:
            break
        shared_prefix += 1
    if shared_prefix < 4:
        return False
    ratio = SequenceMatcher(None, left, right).ratio()
    if shared_prefix >= 5 and abs(len(left) - len(right)) <= 2 and ratio >= 0.76:
        return True
    return ratio >= 0.80


def file_reference_matches(candidate_text: str, file_reference: str) -> bool:
    raw_candidate = str(candidate_text or "").strip().lower()
    target = str(file_reference or "").strip().lower().replace("\\", "/")
    if not raw_candidate or not target:
        return False
    if target in raw_candidate:
        return True

    basename = PurePosixPath(target).name
    stem = PurePosixPath(target).stem
    suffix = PurePosixPath(target).suffix.lstrip(".").lower()
    candidate_norm = normalize_file_reference_text(raw_candidate)
    normalized_variants = {
        normalize_file_reference_text(target),
        normalize_file_reference_text(basename),
        normalize_file_reference_text(stem),
    }
    if any(
        variant
        and len(variant) >= 4
        and (variant in candidate_norm or candidate_norm in variant)
        for variant in normalized_variants
    ):
        return True

    candidate_tokens = file_reference_tokens(raw_candidate)
    if not candidate_tokens:
        return False

    stem_tokens = [token for token in file_reference_tokens(stem) if len(token) >= 4]
    if not stem_tokens:
        return False

    # File extensions are a strong hint that the text is referring to a file
    # rather than a generic similarly-spelled word.
    if suffix and suffix not in candidate_tokens:
        return False

    return any(
        looks_like_file_reference_typo_close_match(stem_token, candidate_token)
        for stem_token in stem_tokens
        for candidate_token in candidate_tokens
    )
