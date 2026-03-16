from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

from config import CFG


class ScreenCaptureError(RuntimeError):
    pass


CAPTURE_MODE_DISPLAY = "display"
CAPTURE_MODE_WINDOW = "window"
CAPTURE_MODE_POINTER = "pointer"
_VALID_CAPTURE_MODES = {
    CAPTURE_MODE_DISPLAY,
    CAPTURE_MODE_WINDOW,
    CAPTURE_MODE_POINTER,
}


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
    raise ScreenCaptureError("PowerShell.exe was not found. Screen snapshot is Windows-only right now.")


def _normalize_capture_mode(mode: str | None) -> str:
    value = str(mode or CAPTURE_MODE_DISPLAY).strip().lower()
    if value not in _VALID_CAPTURE_MODES:
        return CAPTURE_MODE_DISPLAY
    return value


def capture_screen_view_to_path(output_path: Path, *, mode: str = CAPTURE_MODE_DISPLAY) -> Path:
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = out_path.with_name(f"{out_path.stem}.tmp{out_path.suffix or '.jpg'}")
    powershell = _resolve_powershell_exe()
    max_dim = int(getattr(CFG, "SCREEN_CAPTURE_MAX_DIM", 1920))
    focus_width = int(getattr(CFG, "SCREEN_POINTER_FOCUS_WIDTH", 1400))
    focus_height = int(getattr(CFG, "SCREEN_POINTER_FOCUS_HEIGHT", 900))
    capture_mode = _normalize_capture_mode(mode)

    ps_script = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class PiperNative {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
}
"@
$outPath = $env:PIPER_SCREENSHOT_OUT
$mode = [string]$env:PIPER_SCREENSHOT_MODE
if ([string]::IsNullOrWhiteSpace($mode)) { $mode = 'display' }
$mode = $mode.ToLowerInvariant()
$maxDim = [double]$env:PIPER_SCREENSHOT_MAX_DIM
$focusWidth = [int]$env:PIPER_SCREENSHOT_FOCUS_WIDTH
$focusHeight = [int]$env:PIPER_SCREENSHOT_FOCUS_HEIGHT

function New-Rectangle([int]$left, [int]$top, [int]$right, [int]$bottom) {
    $width = [Math]::Max(1, $right - $left)
    $height = [Math]::Max(1, $bottom - $top)
    return New-Object System.Drawing.Rectangle($left, $top, $width, $height)
}

$cursor = [System.Windows.Forms.Cursor]::Position
$screen = [System.Windows.Forms.Screen]::FromPoint($cursor)
$bounds = $screen.Bounds

if ($mode -eq 'window') {
    $hwnd = [PiperNative]::GetForegroundWindow()
    if ($hwnd -ne [IntPtr]::Zero) {
        $rect = New-Object PiperNative+RECT
        if ([PiperNative]::GetWindowRect($hwnd, [ref]$rect)) {
            if (($rect.Right - $rect.Left) -gt 1 -and ($rect.Bottom - $rect.Top) -gt 1) {
                $bounds = New-Rectangle $rect.Left $rect.Top $rect.Right $rect.Bottom
            }
        }
    }
}
elseif ($mode -eq 'pointer') {
    $targetWidth = [Math]::Min([Math]::Max(1, $focusWidth), $screen.Bounds.Width)
    $targetHeight = [Math]::Min([Math]::Max(1, $focusHeight), $screen.Bounds.Height)
    $left = [Math]::Max($screen.Bounds.Left, [Math]::Min($cursor.X - [int]($targetWidth / 2), $screen.Bounds.Right - $targetWidth))
    $top = [Math]::Max($screen.Bounds.Top, [Math]::Min($cursor.Y - [int]($targetHeight / 2), $screen.Bounds.Bottom - $targetHeight))
    $bounds = New-Object System.Drawing.Rectangle($left, $top, $targetWidth, $targetHeight)
}

$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
    $scale = 1.0
    if ($maxDim -gt 0) {
        $scale = [Math]::Min(1.0, [Math]::Min($maxDim / [double]$bounds.Width, $maxDim / [double]$bounds.Height))
    }
    if ($scale -lt 1.0) {
        $newWidth = [int][Math]::Round($bounds.Width * $scale)
        $newHeight = [int][Math]::Round($bounds.Height * $scale)
        $resized = New-Object System.Drawing.Bitmap $newWidth, $newHeight
        $graphics2 = [System.Drawing.Graphics]::FromImage($resized)
        try {
            $graphics2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics2.DrawImage($bitmap, 0, 0, $newWidth, $newHeight)
            $resized.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Jpeg)
        }
        finally {
            $graphics2.Dispose()
            $resized.Dispose()
        }
    }
    else {
        $bitmap.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Jpeg)
    }
    Write-Output $outPath
}
finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}
""".strip()

    proc = subprocess.run(
        [powershell, "-NoProfile", "-Command", ps_script],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PIPER_SCREENSHOT_OUT": _to_windows_path(temp_path),
            "PIPER_SCREENSHOT_MAX_DIM": str(max_dim),
            "PIPER_SCREENSHOT_MODE": capture_mode,
            "PIPER_SCREENSHOT_FOCUS_WIDTH": str(focus_width),
            "PIPER_SCREENSHOT_FOCUS_HEIGHT": str(focus_height),
        },
        timeout=20,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or proc.stdout or "").strip()
        raise ScreenCaptureError(stderr or "PowerShell screen capture failed.")
    if not temp_path.exists():
        raise ScreenCaptureError("PowerShell reported success but no screenshot file was created.")
    temp_path.replace(out_path)
    return out_path


def capture_primary_screen_to_path(output_path: Path) -> Path:
    return capture_screen_view_to_path(output_path, mode=CAPTURE_MODE_DISPLAY)


def capture_primary_screen(*, output_dir: Path | None = None) -> Path:
    base_dir = Path(output_dir or (CFG.DATA_DIR / "workspace" / "images"))
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = base_dir / f"PiperScreen_{stamp}.jpg"
    return capture_primary_screen_to_path(out_path)
