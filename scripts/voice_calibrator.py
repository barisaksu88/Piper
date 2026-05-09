"""Standalone voice calibration tool for Piper.

Lets you enroll speaker profiles, record test samples, and inspect similarity
scores so you can pick real thresholds. Uses the exact same Resemblyzer
preprocessing and embedding pipeline as core/voice_recognition.py.

Usage:
    .venv\\Scripts\\python.exe scripts\\voice_calibrator.py
"""

from __future__ import annotations

import csv
import datetime
import os
import pickle
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is on path so we can import core.voice_recognition
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None  # type: ignore[assignment]

from core.voice_recognition import VoiceFingerprintEngine

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CALIBRATION_DIR = PROJECT_ROOT / "data" / "voice_calibration"
PROFILE_DIR = CALIBRATION_DIR / "profiles"
RESULTS_CSV = CALIBRATION_DIR / "calibration_results.csv"
PIPER_VOICE_DIR = PROJECT_ROOT / "data" / "voice_embeddings"

PROFILE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Audio helpers — matched to tools/stt.py
# ---------------------------------------------------------------------------

SILENCE_RMS_THRESHOLD_FLOAT: float = 0.005   # ~-46 dBFS for float32 audio
SILENCE_RMS_THRESHOLD_INT16: int = 200       # roughly equivalent in int16 space


def _audio_rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio.astype("float64") ** 2)))


def _select_microphone_device() -> Tuple[int, Any]:
    """Pick an input device using the same heuristic as Piper's STT engine."""
    if sd is None:
        raise RuntimeError("sounddevice is not installed")
    devices = sd.query_devices()
    input_devices = [(i, dev) for i, dev in enumerate(devices) if dev["max_input_channels"] > 0]
    if not input_devices:
        raise RuntimeError("No input audio devices available.")

    # Prefer a device with 'mikrofon' in the name, excluding stereo / kar virtual devices
    for i, dev in input_devices:
        name_lower = dev["name"].lower()
        if "mikrofon" in name_lower and "stereo" not in name_lower and "kar" not in name_lower:
            return i, dev

    # Fall back to default input device
    try:
        default_input = sd.default.device[0]
    except Exception:
        default_input = None
    if isinstance(default_input, int) and default_input >= 0:
        dev = devices[default_input]
        if dev["max_input_channels"] > 0:
            return default_input, dev

    # Last resort: first input device
    return input_devices[0]


def _resample_to_16k(audio: np.ndarray, source_sr: int) -> np.ndarray:
    """Linear-interpolation resample, same math Piper uses in STT."""
    if source_sr == 16000:
        return audio.astype("float32")
    source_idx = np.arange(audio.shape[0], dtype=np.float32)
    target_len = max(1, int(round(audio.shape[0] * 16000 / source_sr)))
    target_idx = np.linspace(0, audio.shape[0] - 1, num=target_len, dtype=np.float32)
    return np.interp(target_idx, source_idx, audio.astype(np.float32)).astype("float32")


def record_audio(duration_sec: int = 5) -> Optional[Tuple[np.ndarray, float, int]]:
    """Record mono audio using Piper's device selection and int16 path.

    Returns (audio_float32_16kHz, rms_int16, source_sample_rate) or None on failure.
    """
    if sd is None:
        raise RuntimeError("sounddevice is not installed")

    device_index, device = _select_microphone_device()
    source_sr = int(device.get("default_samplerate") or 16000)

    # Blocked-record exactly like scripts/enroll_voice.py but with explicit device & int16
    frames = int(duration_sec * source_sr)
    audio_int16 = sd.rec(frames, samplerate=source_sr, device=device_index, channels=1, dtype="int16")
    sd.wait()
    audio_int16 = audio_int16.squeeze()

    rms = float(np.sqrt(np.mean(audio_int16.astype(np.float32) ** 2)))

    # Convert to float32 [-1.0, 1.0] and downsample to 16 kHz (same as Piper STT)
    audio_float = audio_int16.astype("float32") / 32768.0
    audio_16k = _resample_to_16k(audio_float, source_sr)

    return audio_16k, rms, source_sr


def load_wav(path: str) -> Tuple[np.ndarray, int]:
    """Load a WAV file and return (samples, sample_rate)."""
    try:
        from scipy.io import wavfile
    except ImportError as exc:
        raise RuntimeError("scipy is required for WAV file loading.") from exc
    sr, data = wavfile.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if data.dtype != "float32":
        if data.dtype.kind == "i":
            data = data.astype("float32") / (2 ** (data.dtype.itemsize * 8 - 1))
        else:
            data = data.astype("float32")
    return data, sr


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def score_against_profile(embedding: np.ndarray, enrolled_embeddings: List[np.ndarray]) -> Dict[str, float]:
    """Compute multiple similarity metrics for a single test embedding."""
    if not enrolled_embeddings:
        return {
            "avg_profile": 0.0,
            "best_individual": 0.0,
        }

    avg_embedding = np.mean(enrolled_embeddings, axis=0)
    avg_sim = cosine_similarity(embedding, avg_embedding)

    best_individual = max(cosine_similarity(embedding, emb) for emb in enrolled_embeddings)

    return {
        "avg_profile": avg_sim,
        "best_individual": best_individual,
    }


def score_test_sample(
    embedding: np.ndarray,
    profiles: Dict[str, List[np.ndarray]],
) -> Dict[str, Any]:
    """Score a test embedding against every saved profile.

    Returns a dict with per-user metrics and overall ranking.
    """
    results: Dict[str, Dict[str, float]] = {}
    for user_id, embs in profiles.items():
        results[user_id] = score_against_profile(embedding, embs)

    # Use avg_profile as the primary ranking metric (same as Piper)
    ranked = sorted(
        ((user_id, data["avg_profile"]) for user_id, data in results.items()),
        key=lambda x: x[1],
        reverse=True,
    )

    best_user = ranked[0][0] if ranked else None
    best_score = ranked[0][1] if ranked else 0.0
    second_user = ranked[1][0] if len(ranked) > 1 else None
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = best_score - second_score

    return {
        "per_user": results,
        "best_user": best_user,
        "best_score": best_score,
        "second_user": second_user,
        "second_score": second_score,
        "margin": margin,
    }


# ---------------------------------------------------------------------------
# CSV persistence
# ---------------------------------------------------------------------------

def append_csv_row(row: Dict[str, Any]) -> None:
    """Append a test-result row to the calibration CSV."""
    fieldnames = [
        "timestamp",
        "test_label",
        "user_id",
        "avg_profile_score",
        "best_individual_score",
        "best_overall_user",
        "best_overall_score",
        "second_overall_user",
        "second_overall_score",
        "margin",
    ]

    file_exists = RESULTS_CSV.exists() and RESULTS_CSV.stat().st_size > 0
    with open(RESULTS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Calibration engine wrapper
# ---------------------------------------------------------------------------

class CalibrationEngine:
    """Thin wrapper around VoiceFingerprintEngine with calibration-specific paths."""

    def __init__(self) -> None:
        self._engine = VoiceFingerprintEngine(data_dir=PROFILE_DIR)
        self._pending: Dict[str, List[np.ndarray]] = {}  # unsaved enrollment buffers
        self._piper_imported: set[str] = set()  # track users that came from Piper production

    @property
    def available(self) -> bool:
        return self._engine.available()

    def list_users(self) -> List[str]:
        return sorted(set(self._engine._embeddings.keys()) | set(self._pending.keys()))

    def user_sample_count(self, user_id: str) -> int:
        return len(self._engine._embeddings.get(user_id, []))

    def is_piper_imported(self, user_id: str) -> bool:
        return user_id in self._piper_imported

    def add_user(self, user_id: str) -> bool:
        user_id = user_id.strip().lower()
        if not user_id:
            return False
        if user_id in self._engine._embeddings or user_id in self._pending:
            return False
        self._pending[user_id] = []
        return True

    def import_piper_profiles(self) -> Tuple[int, List[str]]:
        """Copy existing Piper production embeddings into the calibrator workspace.

        Returns (count imported, list of user_ids).
        """
        imported: List[str] = []
        if not PIPER_VOICE_DIR.exists():
            return 0, imported
        for path in PIPER_VOICE_DIR.glob("*.pkl"):
            user_id = path.stem
            try:
                with open(path, "rb") as f:
                    embeddings = pickle.load(f)
                if not isinstance(embeddings, list) or not embeddings:
                    continue
                # Merge with anything already in calibrator for this user
                existing = self._engine._embeddings.get(user_id, [])
                merged = existing + [e for e in embeddings if isinstance(e, np.ndarray)]
                self._engine.import_profile(user_id, merged, admin=False)
                self._piper_imported.add(user_id)
                imported.append(user_id)
            except Exception:
                continue
        return len(imported), imported

    def record_enrollment_sample(self, user_id: str, duration: int = 5) -> Tuple[Optional[np.ndarray], float, bool]:
        """Record from mic and append to the user's pending buffer.

        Returns (embedding_or_None, rms_int16, is_silent).
        """
        result = record_audio(duration_sec=duration)
        if result is None:
            return None, 0.0, True
        audio, rms, _source_sr = result
        is_silent = rms < SILENCE_RMS_THRESHOLD_INT16
        if is_silent:
            return None, rms, True
        emb = self._engine.extract_embedding(audio, sample_rate=16000)
        if emb is None:
            return None, rms, False
        # Append to pending (saved or unsaved)
        target = self._pending.setdefault(user_id, [])
        # If user already has saved embeddings, keep them together conceptually,
        # but we only mutate pending so Save is explicit.
        target.append(emb)
        return emb, rms, False

    def pending_count(self, user_id: str) -> int:
        return len(self._pending.get(user_id, []))

    def save_profile(self, user_id: str) -> bool:
        """Persist pending embeddings to disk."""
        pending = self._pending.get(user_id, [])
        if not pending:
            return False
        # Merge with any existing saved embeddings
        existing = self._engine._embeddings.get(user_id, [])
        merged = existing + pending
        self._engine.import_profile(user_id, merged, admin=False)
        self._pending[user_id] = []
        return True

    def delete_user(self, user_id: str) -> None:
        self._engine.forget_user(user_id)
        self._pending.pop(user_id, None)
        self._piper_imported.discard(user_id)

    def get_profiles(self) -> Dict[str, List[np.ndarray]]:
        """Return all saved profiles (not pending)."""
        return {uid: list(embs) for uid, embs in self._engine._embeddings.items()}

    def extract_embedding_from_file(self, path: str) -> Optional[np.ndarray]:
        try:
            audio, sr = load_wav(path)
            return self._engine.extract_embedding(audio, sample_rate=sr)
        except Exception:
            return None

    def extract_embedding_from_mic(self, duration: int = 5) -> Tuple[Optional[np.ndarray], float, bool]:
        """Record and extract embedding. Returns (embedding_or_None, rms_int16, is_silent)."""
        result = record_audio(duration_sec=duration)
        if result is None:
            return None, 0.0, True
        audio, rms, _source_sr = result
        is_silent = rms < SILENCE_RMS_THRESHOLD_INT16
        if is_silent:
            return None, rms, True
        emb = self._engine.extract_embedding(audio, sample_rate=16000)
        return emb, rms, False


# ---------------------------------------------------------------------------
# Tkinter UI
# ---------------------------------------------------------------------------

class VoiceCalibratorApp:
    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        master.title("Piper Voice Calibrator")
        master.geometry("900x700")
        master.minsize(800, 600)

        self.engine = CalibrationEngine()
        if not self.engine.available:
            messagebox.showerror(
                "Missing Dependency",
                "Resemblyzer is not installed.\n\nInstall it with:\n  pip install resemblyzer",
            )
            master.destroy()
            return

        if sd is None:
            messagebox.showerror(
                "Missing Dependency",
                "sounddevice is not installed.\n\nInstall it with:\n  pip install sounddevice",
            )
            master.destroy()
            return

        self._recording = False
        self._last_recorded_emb: Optional[np.ndarray] = None
        self._build_ui()
        self._refresh_user_list()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # Top frame: people list + actions
        top_frame = tk.Frame(self.master, padx=10, pady=10)
        top_frame.pack(fill=tk.X)

        tk.Label(top_frame, text="People", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W)

        list_row = tk.Frame(top_frame)
        list_row.pack(fill=tk.X, pady=(4, 0))

        self.user_listbox = tk.Listbox(list_row, height=6, exportselection=False)
        self.user_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.user_listbox.bind("<<ListboxSelect>>", self._on_user_select)

        list_scroll = tk.Scrollbar(list_row, orient=tk.VERTICAL, command=self.user_listbox.yview)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.user_listbox.config(yscrollcommand=list_scroll.set)

        btn_row = tk.Frame(top_frame)
        btn_row.pack(fill=tk.X, pady=(6, 0))

        tk.Button(btn_row, text="Add Person", command=self._add_person).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_row, text="Delete Person", command=self._delete_person).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_row, text="Import from Piper", command=self._import_piper).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_row, text="Check Mic", command=self._check_mic).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_row, text="Refresh", command=self._refresh_user_list).pack(side=tk.LEFT)

        # Separator
        tk.Frame(self.master, height=2, bg="#cccccc").pack(fill=tk.X, padx=10, pady=6)

        # Middle frame: Enrollment
        enroll_frame = tk.LabelFrame(self.master, text="Enrollment", padx=10, pady=10)
        enroll_frame.pack(fill=tk.X, padx=10, pady=(0, 6))

        self.selected_label = tk.Label(enroll_frame, text="Selected: (none)", font=("Segoe UI", 9, "italic"))
        self.selected_label.pack(anchor=tk.W)

        self.samples_label = tk.Label(enroll_frame, text="Saved samples: 0 | Pending: 0")
        self.samples_label.pack(anchor=tk.W, pady=(4, 0))

        enroll_btn_row = tk.Frame(enroll_frame)
        enroll_btn_row.pack(fill=tk.X, pady=(8, 0))

        tk.Label(enroll_btn_row, text="Duration (s):").pack(side=tk.LEFT)
        self.duration_var = tk.StringVar(value="5")
        tk.Spinbox(enroll_btn_row, from_=2, to=30, textvariable=self.duration_var, width=4).pack(side=tk.LEFT, padx=(4, 12))

        self.record_btn = tk.Button(enroll_btn_row, text="Record Enrollment Sample", command=self._record_enrollment)
        self.record_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.save_btn = tk.Button(enroll_btn_row, text="Save Profile", command=self._save_profile)
        self.save_btn.pack(side=tk.LEFT, padx=(0, 6))

        # Separator
        tk.Frame(self.master, height=2, bg="#cccccc").pack(fill=tk.X, padx=10, pady=6)

        # Bottom frame: Test
        test_frame = tk.LabelFrame(self.master, text="Test", padx=10, pady=10)
        test_frame.pack(fill=tk.X, padx=10, pady=(0, 6))

        test_btn_row = tk.Frame(test_frame)
        test_btn_row.pack(fill=tk.X)

        tk.Label(test_btn_row, text="Duration (s):").pack(side=tk.LEFT)
        self.test_duration_var = tk.StringVar(value="5")
        tk.Spinbox(test_btn_row, from_=2, to=30, textvariable=self.test_duration_var, width=4).pack(side=tk.LEFT, padx=(4, 12))

        self.test_record_btn = tk.Button(test_btn_row, text="Record Test Sample", command=self._record_test)
        self.test_record_btn.pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(test_btn_row, text="Load WAV Test Sample…", command=self._load_test_wav).pack(side=tk.LEFT)

        # Results text area
        self.results_text = tk.Text(test_frame, height=14, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 10))
        self.results_text.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        # CSV status
        self.csv_label = tk.Label(self.master, text=f"CSV: {RESULTS_CSV}", fg="#666666", font=("Segoe UI", 8))
        self.csv_label.pack(anchor=tk.W, padx=12, pady=(0, 6))

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = tk.Label(self.master, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _refresh_user_list(self) -> None:
        self.user_listbox.delete(0, tk.END)
        for uid in self.engine.list_users():
            count = self.engine.user_sample_count(uid)
            pending = self.engine.pending_count(uid)
            tag = " [PIPER]" if self.engine.is_piper_imported(uid) else ""
            label = f"{uid}{tag}  ({count} saved"
            if pending:
                label += f", {pending} pending"
            label += ")"
            self.user_listbox.insert(tk.END, label)
        self._update_selection_ui()

    def _selected_user_id(self) -> Optional[str]:
        sel = self.user_listbox.curselection()
        if not sel:
            return None
        text: str = self.user_listbox.get(sel[0])
        # Format: "user_id [PIPER]  (count saved, pending)"
        # or:     "user_id  (count saved, pending)"
        first_token = text.split()[0].strip().lower()
        return first_token

    def _on_user_select(self, _event: Any = None) -> None:
        self._update_selection_ui()

    def _update_selection_ui(self) -> None:
        user_id = self._selected_user_id()
        if user_id:
            self.selected_label.config(text=f"Selected: {user_id}")
            saved = self.engine.user_sample_count(user_id)
            pending = self.engine.pending_count(user_id)
            self.samples_label.config(text=f"Saved samples: {saved} | Pending: {pending}")
            self.save_btn.config(state=tk.NORMAL if pending > 0 else tk.DISABLED)
        else:
            self.selected_label.config(text="Selected: (none)")
            self.samples_label.config(text="Saved samples: 0 | Pending: 0")
            self.save_btn.config(state=tk.DISABLED)

    def _import_piper(self) -> None:
        count, imported = self.engine.import_piper_profiles()
        if count == 0:
            messagebox.showinfo(
                "No Profiles Found",
                f"No production profiles found in:\n{PIPER_VOICE_DIR}",
            )
            return
        self._refresh_user_list()
        self.status_var.set(f"Imported {count} Piper profile(s): {', '.join(imported)}")

    def _add_person(self) -> None:
        name = simpledialog.askstring("Add Person", "Enter person name:", parent=self.master)
        if not name:
            return
        name = name.strip().lower()
        if not name:
            return
        success = self.engine.add_user(name)
        if not success:
            messagebox.showwarning("Duplicate", f"'{name}' already exists.")
            return
        self._refresh_user_list()
        # Select the new user
        prefix = f"{name}  ("
        for i in range(self.user_listbox.size()):
            if self.user_listbox.get(i).startswith(prefix):
                self.user_listbox.selection_set(i)
                self.user_listbox.see(i)
                break
        self._update_selection_ui()

    def _delete_person(self) -> None:
        user_id = self._selected_user_id()
        if not user_id:
            messagebox.showinfo("Select User", "Please select a person to delete.")
            return
        if self.engine.is_piper_imported(user_id):
            if not messagebox.askyesno(
                "Confirm",
                f"'{user_id}' was imported from Piper's production profiles.\n\n"
                f"Deleting it here only removes it from the calibrator — "
                f"Piper's original file at data/voice_embeddings/ will not be touched.\n\n"
                f"Delete from calibrator anyway?",
            ):
                return
        else:
            if not messagebox.askyesno("Confirm", f"Delete profile for '{user_id}'?"):
                return
        self.engine.delete_user(user_id)
        self._refresh_user_list()
        self._clear_results()

    def _record_enrollment(self) -> None:
        user_id = self._selected_user_id()
        if not user_id:
            messagebox.showinfo("Select User", "Please select a person first.")
            return
        if self._recording:
            return
        self._recording = True
        self.record_btn.config(state=tk.DISABLED, text="Recording…")
        self.status_var.set("Recording enrollment sample…")

        def target() -> None:
            try:
                duration = int(self.duration_var.get() or "5")
            except ValueError:
                duration = 5
            emb, rms, is_silent = self.engine.record_enrollment_sample(user_id, duration=duration)
            self.master.after(0, lambda: self._on_enrollment_done(emb, rms, is_silent))

        threading.Thread(target=target, daemon=True).start()

    def _on_enrollment_done(self, emb: Optional[np.ndarray], rms: float, is_silent: bool) -> None:
        self._recording = False
        self.record_btn.config(state=tk.NORMAL, text="Record Enrollment Sample")
        if is_silent:
            self.status_var.set(f"Recording rejected — silence detected (RMS {rms:.0f}).")
            messagebox.showwarning(
                "Silence Detected",
                f"The recording appears to be silence (RMS {rms:.0f}).\n\n"
                f"Possible causes:\n"
                f"  • Microphone is muted\n"
                f"  • Windows privacy settings blocked Python mic access\n"
                f"  • Wrong recording device selected\n\n"
                f"Check your mic and try again.",
            )
            return
        if emb is None:
            self.status_var.set("Failed to extract embedding.")
            messagebox.showwarning("Extraction Failed", "Could not extract an embedding from the recording.")
            return
        self.status_var.set(f"Enrollment sample captured (RMS {rms:.0f}).")
        self._last_recorded_emb = emb
        self._refresh_user_list()

    def _save_profile(self) -> None:
        user_id = self._selected_user_id()
        if not user_id:
            return
        ok = self.engine.save_profile(user_id)
        if ok:
            self.status_var.set(f"Profile saved for '{user_id}'.")
        self._refresh_user_list()

    def _record_test(self) -> None:
        if self._recording:
            return
        self._recording = True
        self.test_record_btn.config(state=tk.DISABLED, text="Recording…")
        self.status_var.set("Recording test sample…")

        def target() -> None:
            try:
                duration = int(self.test_duration_var.get() or "5")
            except ValueError:
                duration = 5
            emb, rms, is_silent = self.engine.extract_embedding_from_mic(duration=duration)
            self.master.after(0, lambda: self._on_test_done(emb, rms, is_silent, source="mic"))

        threading.Thread(target=target, daemon=True).start()

    def _load_test_wav(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select WAV file",
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")],
        )
        if not path:
            return
        emb = self.engine.extract_embedding_from_file(path)
        if emb is None:
            messagebox.showerror("Error", f"Failed to load or process:\n{path}")
            return
        self._on_test_done(emb, source=Path(path).name)

    def _on_test_done(self, embedding: Optional[np.ndarray], rms: float, is_silent: bool, source: str) -> None:
        self._recording = False
        self.test_record_btn.config(state=tk.NORMAL, text="Record Test Sample")

        if is_silent:
            self.status_var.set(f"Test rejected — silence detected (RMS {rms:.0f}).")
            messagebox.showwarning(
                "Silence Detected",
                f"The recording appears to be silence (RMS {rms:.0f}).\n\n"
                f"Possible causes:\n"
                f"  • Microphone is muted\n"
                f"  • Windows privacy settings blocked Python mic access\n"
                f"  • Wrong recording device selected\n\n"
                f"Check your mic and try again.",
            )
            return

        if embedding is None:
            self.status_var.set("Test recording failed.")
            messagebox.showwarning("Extraction Failed", "Could not extract an embedding from the test sample.")
            return

        # Detect identical consecutive recordings (stale/cached mic buffer)
        if self._last_recorded_emb is not None:
            dup_sim = cosine_similarity(embedding, self._last_recorded_emb)
            if dup_sim >= 0.9999:
                self.status_var.set(f"Warning: recording is identical to previous (sim {dup_sim:.4f}).")
                messagebox.showwarning(
                    "Duplicate Recording",
                    f"This recording is byte-for-byte identical to the previous one.\n\n"
                    f"Your microphone may be returning a cached silence buffer.\n"
                    f"Check Windows mic privacy settings and device selection.",
                )
        self._last_recorded_emb = embedding

        profiles = self.engine.get_profiles()
        if not profiles:
            self.status_var.set("No saved profiles to compare against.")
            messagebox.showinfo("No Profiles", "Enroll and save at least one person first.")
            return

        results = score_test_sample(embedding, profiles)
        self._display_results(results, source, rms=rms)
        self._write_csv(results, source)
        self.status_var.set(f"Test complete. Best match: {results['best_user']} ({results['best_score']:.4f})")

    def _check_mic(self) -> None:
        if self._recording:
            return
        self._recording = True
        self.status_var.set("Checking microphone (2 sec)…")

        def target() -> None:
            result = record_audio(duration_sec=2)
            self.master.after(0, lambda: self._on_check_mic_done(result))

        threading.Thread(target=target, daemon=True).start()

    def _on_check_mic_done(self, result: Optional[Tuple[np.ndarray, float, int]]) -> None:
        self._recording = False
        if result is None:
            self.status_var.set("Mic check failed.")
            messagebox.showerror("Mic Check", "Failed to record audio.")
            return
        audio, rms, source_sr = result
        is_silent = rms < SILENCE_RMS_THRESHOLD_INT16
        msg = f"RMS level: {rms:.1f}\nSample rate: {source_sr} Hz\n"
        if is_silent:
            msg += (
                f"\nThis is effectively silence (threshold: {SILENCE_RMS_THRESHOLD_INT16}).\n\n"
                f"Check your mic is not muted and Windows allows Python to use it."
            )
            messagebox.showwarning("Mic Check — Silence Detected", msg)
        else:
            msg += "\nMicrophone is picking up audio."
            messagebox.showinfo("Mic Check — OK", msg)
        self.status_var.set(f"Mic check: RMS {rms:.1f} @ {source_sr} Hz {'SILENCE' if is_silent else 'OK'}")

    def _display_results(self, results: Dict[str, Any], source: str, rms: Optional[float] = None) -> None:
        self.results_text.config(state=tk.NORMAL)
        self.results_text.delete("1.0", tk.END)

        lines: List[str] = []
        lines.append(f"Test sample: {source}")
        if rms is not None:
            lines.append(f"Recording RMS: {rms:.5f}")
        lines.append(f"Timestamp: {datetime.datetime.now().isoformat(sep=' ', timespec='seconds')}")
        lines.append("")
        lines.append(f"{'User':<16} {'Avg Profile':>12} {'Best Sample':>12}")
        lines.append("-" * 42)

        per_user: Dict[str, Dict[str, float]] = results["per_user"]
        for user_id in sorted(per_user.keys()):
            data = per_user[user_id]
            lines.append(
                f"{user_id:<16} {data['avg_profile']:>12.4f} {data['best_individual']:>12.4f}"
            )

        lines.append("")
        lines.append(f"Best match:       {results['best_user']}  {results['best_score']:.4f}")
        lines.append(f"Second best:      {results['second_user']}  {results['second_score']:.4f}")
        lines.append(f"Margin:           {results['margin']:.4f}")

        # Threshold hints
        lines.append("")
        lines.append("Threshold hints:")
        lines.append(f"  High (regular): 0.85")
        lines.append(f"  High (admin):   0.90")
        lines.append(f"  Low / infer:    0.60")

        self.results_text.insert(tk.END, "\n".join(lines))
        self.results_text.config(state=tk.DISABLED)

    def _write_csv(self, results: Dict[str, Any], source: str) -> None:
        per_user: Dict[str, Dict[str, float]] = results["per_user"]
        timestamp = datetime.datetime.now().isoformat(sep=" ", timespec="seconds")
        for user_id, data in per_user.items():
            append_csv_row({
                "timestamp": timestamp,
                "test_label": source,
                "user_id": user_id,
                "avg_profile_score": f"{data['avg_profile']:.6f}",
                "best_individual_score": f"{data['best_individual']:.6f}",
                "best_overall_user": results.get("best_user", ""),
                "best_overall_score": f"{results.get('best_score', 0.0):.6f}",
                "second_overall_user": results.get("second_user", ""),
                "second_overall_score": f"{results.get('second_score', 0.0):.6f}",
                "margin": f"{results.get('margin', 0.0):.6f}",
            })

    def _clear_results(self) -> None:
        self.results_text.config(state=tk.NORMAL)
        self.results_text.delete("1.0", tk.END)
        self.results_text.config(state=tk.DISABLED)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    root = tk.Tk()
    app = VoiceCalibratorApp(root)
    if app.engine.available and sd is not None:
        root.mainloop()


if __name__ == "__main__":
    main()
