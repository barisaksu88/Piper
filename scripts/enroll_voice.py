r"""Voice enrollment utility for Piper.

Creates a voice profile from microphone recordings or WAV files.
For admin users, the profile is marked with a stricter match threshold.

Usage:
    # Record from mic (5 takes, 5 seconds each)
    .venv\Scripts\python.exe scripts\enroll_voice.py --user baris --admin

    # Load from WAV files
    .venv\Scripts\python.exe scripts\enroll_voice.py --user baris --admin --wav "C:\Users\Baris\voice\*.wav"

    # List enrolled users
    .venv\Scripts\python.exe scripts\enroll_voice.py --list

    # Delete a profile
    .venv\Scripts\python.exe scripts\enroll_voice.py --delete baris
"""

from __future__ import annotations

import argparse
import glob
import os
import pickle
import sys
import time
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _get_engine():
    from core.voice_recognition import get_voice_engine
    from config import CFG

    if not CFG.VOICE_RECOGNITION_ENABLED:
        print("[ERROR] Voice recognition is disabled in config.")
        sys.exit(1)
    engine = get_voice_engine()
    if not engine.available():
        print("[ERROR] Resemblyzer is not installed.")
        sys.exit(1)
    return engine


def _record_take(duration_sec: int = 5, sample_rate: int = 16000) -> tuple:
    """Record one audio take from the default microphone."""
    import numpy as np
    import sounddevice as sd

    print(f"  Recording {duration_sec}s... ", end="", flush=True)
    audio = sd.rec(int(duration_sec * sample_rate), samplerate=sample_rate, channels=1, dtype="float32")
    sd.wait()
    print("done.")
    return audio.squeeze(), sample_rate


def _load_wav(path: str) -> tuple:
    """Load a WAV file and return (samples, sample_rate)."""
    try:
        from scipy.io import wavfile
    except ImportError:
        print("[ERROR] scipy is required for WAV file loading.")
        sys.exit(1)
    sr, data = wavfile.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if data.dtype != "float32":
        if data.dtype.kind == "i":
            data = data.astype("float32") / (2 ** (data.dtype.itemsize * 8 - 1))
        else:
            data = data.astype("float32")
    return data, sr


def cmd_record(args) -> None:
    engine = _get_engine()
    user_id = args.user.strip().lower()
    takes = args.takes
    duration = args.duration

    print(f"[Voice Enrollment] User: {user_id} | Admin: {args.admin} | Takes: {takes} x {duration}s")
    print("Speak clearly and naturally. Press Enter to begin...")
    input()

    embeddings = []
    for i in range(1, takes + 1):
        print(f"Take {i}/{takes}:")
        audio, sr = _record_take(duration_sec=duration, sample_rate=16000)
        emb = engine.extract_embedding(audio, sample_rate=sr)
        if emb is None:
            print("  [WARN] Failed to extract embedding. Skipping this take.")
            continue
        embeddings.append(emb)
        print(f"  Embedding extracted ({len(emb)} dims).")

    if not embeddings:
        print("[ERROR] No valid embeddings extracted.")
        sys.exit(1)

    engine.import_profile(user_id, embeddings, admin=args.admin)
    print(f"[OK] Saved {len(embeddings)} embeddings for '{user_id}'.")
    print(f"     File: {engine._embedding_path(user_id)}")
    if args.admin:
        print(f"     Admin meta updated (threshold: higher).")


def cmd_wav(args) -> None:
    engine = _get_engine()
    user_id = args.user.strip().lower()
    pattern = args.wav

    paths = glob.glob(pattern, recursive=False)
    if not paths:
        print(f"[ERROR] No files matched pattern: {pattern}")
        sys.exit(1)

    print(f"[Voice Enrollment] User: {user_id} | Admin: {args.admin}")
    print(f"Loading {len(paths)} WAV file(s)...")

    embeddings = []
    for p in sorted(paths):
        print(f"  {p} ... ", end="", flush=True)
        try:
            audio, sr = _load_wav(p)
            emb = engine.extract_embedding(audio, sample_rate=sr)
            if emb is None:
                print("SKIP (extraction failed)")
                continue
            embeddings.append(emb)
            print(f"OK ({len(emb)} dims)")
        except Exception as exc:
            print(f"ERROR ({exc})")

    if not embeddings:
        print("[ERROR] No valid embeddings extracted.")
        sys.exit(1)

    engine.import_profile(user_id, embeddings, admin=args.admin)
    print(f"[OK] Saved {len(embeddings)} embeddings for '{user_id}'.")
    print(f"     File: {engine._embedding_path(user_id)}")
    if args.admin:
        print(f"     Admin meta updated (threshold: higher).")


def cmd_list(_args) -> None:
    engine = _get_engine()
    admin_users = getattr(engine, "_admin_users", set())
    print("[Enrolled Users]")
    if not engine._embeddings:
        print("  (none)")
        return
    for user_id, embs in sorted(engine._embeddings.items()):
        admin_flag = " [ADMIN]" if user_id in admin_users else ""
        print(f"  {user_id}: {len(embs)} embedding(s){admin_flag}")


def cmd_delete(args) -> None:
    engine = _get_engine()
    user_id = args.delete.strip().lower()
    engine.forget_user(user_id)
    print(f"[OK] Deleted voice profile for '{user_id}'.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Piper voice enrollment utility")
    parser.add_argument("--user", type=str, default="", help="User ID for the profile")
    parser.add_argument("--admin", action="store_true", help="Mark as admin (stricter threshold)")
    parser.add_argument("--takes", type=int, default=5, help="Number of recording takes (default: 5)")
    parser.add_argument("--duration", type=int, default=5, help="Seconds per take (default: 5)")
    parser.add_argument("--wav", type=str, default="", help="Glob pattern for WAV file(s)")
    parser.add_argument("--list", action="store_true", dest="list_users", help="List enrolled users")
    parser.add_argument("--delete", type=str, default="", help="Delete a user's voice profile")

    args = parser.parse_args()

    if args.list_users:
        cmd_list(args)
    elif args.delete:
        cmd_delete(args)
    elif args.wav:
        if not args.user:
            parser.error("--user is required with --wav")
        cmd_wav(args)
    elif args.user:
        cmd_record(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
