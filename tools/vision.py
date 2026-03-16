from __future__ import annotations

import base64
import mimetypes
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from config import CFG
from core.instructions_loader import InstructionLoader
from llm.llm_server_client import LLMClientError
from core.runtime_control import CancellationToken, OperationCancelled


class VisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class VisionRequest:
    image_path: str
    question: str


@dataclass(frozen=True)
class VisionResolvedRequest:
    image_path: Path
    question: str


_WINDOWS_PATH_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$")
_IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".gif",
}
_ATTACHMENT_FALLBACK_MAX_DIMS = (1600, 1280, 1024, 768)


def _coerce_existing_path(raw_path: str) -> Optional[Path]:
    raw = str(raw_path or "").strip()
    if not raw:
        return None

    direct = Path(raw)
    if direct.exists():
        return direct

    windows_match = _WINDOWS_PATH_RE.match(raw)
    if windows_match and os.name != "nt":
        drive = windows_match.group(1).lower()
        suffix = windows_match.group(2).replace("\\", "/")
        wsl_path = Path(f"/mnt/{drive}/{suffix}")
        if wsl_path.exists():
            return wsl_path

    if os.name == "nt" and raw.startswith("/mnt/") and len(raw) > 6:
        drive = raw[5]
        suffix = raw[7:].replace("/", "\\")
        win_path = Path(f"{drive.upper()}:\\{suffix}")
        if win_path.exists():
            return win_path

    return None


def _candidate_paths(raw_path: str) -> Iterable[Path]:
    normalized = str(raw_path or "").strip().strip('"').strip("'")
    if not normalized:
        return ()

    direct = _coerce_existing_path(normalized)
    candidates: List[Path] = []
    if direct is not None:
        candidates.append(direct)

    normalized_rel = normalized.replace("\\", "/").lstrip("./")
    rel_path = Path(normalized_rel)
    workspace_dir = CFG.DATA_DIR / "workspace"
    for candidate in (
        Path(normalized),
        CFG.ROOT_DIR / rel_path,
        workspace_dir / rel_path,
        workspace_dir / "images" / rel_path.name,
    ):
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def resolve_vision_request(request: VisionRequest) -> VisionResolvedRequest:
    for candidate in _candidate_paths(request.image_path):
        if candidate.exists() and candidate.is_file():
            if candidate.suffix.lower() not in _IMAGE_SUFFIXES:
                raise VisionError(f"Unsupported image type: {candidate.suffix or '(none)'}")
            return VisionResolvedRequest(image_path=candidate, question=request.question.strip())
    raise VisionError(f"Image not found: {request.image_path}")


def _guess_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    if suffix == ".bmp":
        return "image/bmp"
    return "application/octet-stream"


def encode_image_data_url(path: Path) -> str:
    mime_type = _guess_mime_type(path)
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _to_windows_path(path: Path) -> str:
    raw = str(Path(path))
    if os.name == "nt":
        return raw
    if raw.startswith("/mnt/") and len(raw) > 6:
        drive = raw[5].upper()
        suffix = raw[7:].replace("/", "\\")
        return f"{drive}:\\{suffix}"
    return raw


def _resolve_powershell_exe() -> str:
    candidates = [
        Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
        Path(r"C:\Windows\System32\powershell.exe"),
    ]
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
                return str(candidate)
        except Exception:
            continue
    raise VisionError("PowerShell.exe was not found for image resize fallback.")


def _resize_image_to_temp_jpeg(image_path: Path, *, max_dim: int) -> Path:
    fd, temp_name = tempfile.mkstemp(prefix="piper-vision-", suffix=".jpg")
    os.close(fd)
    temp_path = Path(temp_name)
    powershell = _resolve_powershell_exe()
    ps_script = r"""
Add-Type -AssemblyName System.Drawing
$inPath = $env:PIPER_VISION_IN
$outPath = $env:PIPER_VISION_OUT
$maxDim = [double]$env:PIPER_VISION_MAX_DIM
$image = [System.Drawing.Image]::FromFile($inPath)
try {
    $scale = 1.0
    if ($maxDim -gt 0) {
        $scale = [Math]::Min(1.0, [Math]::Min($maxDim / [double]$image.Width, $maxDim / [double]$image.Height))
    }
    $newWidth = [int][Math]::Round($image.Width * $scale)
    $newHeight = [int][Math]::Round($image.Height * $scale)
    if ($newWidth -lt 1) { $newWidth = 1 }
    if ($newHeight -lt 1) { $newHeight = 1 }
    $bitmap = New-Object System.Drawing.Bitmap $newWidth, $newHeight
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.DrawImage($image, 0, 0, $newWidth, $newHeight)
        $bitmap.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Jpeg)
    }
    finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
    Write-Output $outPath
}
finally {
    $image.Dispose()
}
""".strip()
    try:
        proc = subprocess.run(
            [powershell, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PIPER_VISION_IN": _to_windows_path(image_path),
                "PIPER_VISION_OUT": _to_windows_path(temp_path),
                "PIPER_VISION_MAX_DIM": str(int(max_dim)),
            },
            timeout=20,
        )
        if proc.returncode != 0 or not temp_path.exists():
            stderr = (proc.stderr or proc.stdout or "").strip()
            raise VisionError(stderr or f"Image resize fallback failed for {image_path.name}.")
        return temp_path
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _coerce_text_content(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return str(content or "").strip()

    parts: List[str] = []
    for item in content:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        if str(item.get("type") or "").strip().lower() in {"text", "input_text"}:
            parts.append(str(item.get("text") or ""))
    return "\n".join(part for part in parts if part).strip()


def build_vision_system_prompt(*, style_overlay: str = "") -> str:
    instructions = InstructionLoader(CFG.INSTRUCTIONS_PATH, max_chars=4000).load().strip()
    parts = [instructions] if instructions else []
    if style_overlay.strip():
        parts.append("[STYLE]\n" + style_overlay.strip())
    parts.append(
        "[VISION_RULES]\n"
        "The user supplied an image and a question.\n"
        "Answer from the image evidence first.\n"
        "If something is unclear, say so directly.\n"
        "Do not invent fine details that are not visible.\n"
        "Keep the answer concise unless the user explicitly asked for depth."
    )
    return "\n\n".join(part for part in parts if part).strip()


def _vision_message_variants(*, data_url: str, question: str, system_prompt: str) -> List[List[Dict[str, Any]]]:
    return [
        [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": question},
                    {"type": "input_image", "image_url": {"url": data_url}},
                ],
            },
        ],
        [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": question},
                    {"type": "input_image", "image_url": data_url},
                ],
            },
        ],
        [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": data_url},
                ],
            },
        ],
    ]


def _image_part(part_type: str, data_url: str, *, url_as_object: bool) -> Dict[str, Any]:
    if part_type == "input_image":
        return {"type": part_type, "image_url": {"url": data_url} if url_as_object else data_url}
    return {"type": part_type, "image_url": {"url": data_url} if url_as_object else data_url}


def build_message_variants_with_image(
    messages: List[Dict[str, Any]],
    *,
    image_path: Path,
    attachment_text: str,
) -> List[List[Dict[str, Any]]]:
    data_url = encode_image_data_url(image_path)
    target_index: Optional[int] = None
    target_text = attachment_text.strip()
    for index in range(len(messages) - 1, -1, -1):
        if str(messages[index].get("role") or "").strip().lower() != "user":
            continue
        target_index = index
        original_text = _coerce_text_content(messages[index].get("content"))
        if original_text:
            target_text = f"{original_text}\n\n{attachment_text.strip()}" if attachment_text.strip() else original_text
        break

    variants: List[List[Dict[str, Any]]] = []
    for text_type, image_type, url_as_object in (
        ("input_text", "input_image", True),
        ("input_text", "input_image", False),
        ("text", "image_url", True),
        ("text", "image_url", False),
    ):
        updated: List[Dict[str, Any]] = [dict(message) for message in messages]
        content = [
            {"type": text_type, "text": target_text},
            _image_part(image_type, data_url, url_as_object=url_as_object),
        ]
        if target_index is None:
            updated.append({"role": "user", "content": content})
        else:
            replaced = dict(updated[target_index])
            replaced["content"] = content
            updated[target_index] = replaced
        variants.append(updated)
    return variants


def _stream_with_single_image_candidate(
    llm_client,
    *,
    messages: List[Dict[str, Any]],
    image_path: Path,
    attachment_text: str,
    temperature: float,
    max_tokens: int | None = None,
    cancel_token: CancellationToken | None = None,
) -> Iterator[str]:
    errors: List[str] = []
    for candidate in build_message_variants_with_image(
        messages,
        image_path=image_path,
        attachment_text=attachment_text,
    ):
        try:
            for delta in llm_client.generate_stream(
                candidate,
                temperature=temperature,
                max_tokens=max_tokens,
                cancel_token=cancel_token,
            ):
                if cancel_token is not None:
                    cancel_token.raise_if_cancelled()
                yield delta
            return
        except OperationCancelled:
            raise
        except LLMClientError as exc:
            errors.append(str(exc))
        except Exception as exc:
            errors.append(str(exc))
    joined = " | ".join(error for error in errors if error) or "Unknown multimodal error."
    raise VisionError(f"Vision request failed. {joined}")


def generate_stream_with_image_attachment(
    llm_client,
    *,
    messages: List[Dict[str, Any]],
    image_path: Path,
    attachment_text: str,
    temperature: float,
    max_tokens: int | None = None,
    cancel_token: CancellationToken | None = None,
) -> Iterator[str]:
    cleanup_paths: List[Path] = []
    candidate_paths: List[Path] = [image_path]
    errors: List[str] = []
    try:
        for candidate_path in candidate_paths:
            try:
                for delta in _stream_with_single_image_candidate(
                    llm_client,
                    messages=messages,
                    image_path=candidate_path,
                    attachment_text=attachment_text,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    cancel_token=cancel_token,
                ):
                    yield delta
                return
            except OperationCancelled:
                raise
            except VisionError as exc:
                errors.append(str(exc))
                if candidate_path != image_path:
                    continue
                for max_dim in _ATTACHMENT_FALLBACK_MAX_DIMS:
                    try:
                        resized = _resize_image_to_temp_jpeg(image_path, max_dim=max_dim)
                    except Exception as resize_exc:
                        errors.append(f"Resize fallback {max_dim}px failed: {resize_exc}")
                        continue
                    cleanup_paths.append(resized)
                    candidate_paths.append(resized)
        joined = " | ".join(error for error in errors if error) or "Unknown multimodal error."
        raise VisionError(joined if joined.startswith("Vision request failed.") else f"Vision request failed. {joined}")
    finally:
        for path in cleanup_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


def generate_with_image_attachment(
    llm_client,
    *,
    messages: List[Dict[str, Any]],
    image_path: Path,
    attachment_text: str,
    temperature: float,
    max_tokens: int | None = None,
    cancel_token: CancellationToken | None = None,
) -> str:
    chunks: List[str] = []
    for delta in generate_stream_with_image_attachment(
        llm_client,
        messages=messages,
        image_path=image_path,
        attachment_text=attachment_text,
        temperature=temperature,
        max_tokens=max_tokens,
        cancel_token=cancel_token,
    ):
        chunks.append(delta)
    return "".join(chunks).strip()


def analyze_image(
    llm_client,
    *,
    request: VisionResolvedRequest,
    style_overlay: str = "",
    temperature: float = 0.2,
    max_tokens: int = 400,
    cancel_token: CancellationToken | None = None,
) -> str:
    system_prompt = build_vision_system_prompt(style_overlay=style_overlay)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": request.question},
    ]
    answer = generate_with_image_attachment(
        llm_client,
        messages=messages,
        image_path=request.image_path,
        attachment_text="Use the attached image to answer the user's question directly.",
        temperature=temperature,
        max_tokens=max_tokens,
        cancel_token=cancel_token,
    )
    if answer:
        return answer
    raise VisionError("Vision request failed. Model returned an empty response.")
