#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True)


def human_bytes(num: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}TiB"


def parse_free_bytes() -> dict[str, int] | None:
    proc = run(["free", "-b"])
    if proc.returncode != 0:
        return None
    lines = [line.split() for line in proc.stdout.splitlines() if line.strip()]
    mem_line = next((parts for parts in lines if parts[0] == "Mem:"), None)
    if not mem_line or len(mem_line) < 7:
        return None
    return {
        "total": int(mem_line[1]),
        "used": int(mem_line[2]),
        "free": int(mem_line[3]),
        "shared": int(mem_line[4]),
        "buff_cache": int(mem_line[5]),
        "available": int(mem_line[6]),
    }


def top_wsl_processes(limit: int = 10) -> list[dict[str, Any]]:
    proc = run(["ps", "-eo", "pid,ppid,rss,%mem,%cpu,etime,cmd", "--sort=-rss"])
    if proc.returncode != 0:
        return []
    lines = [line.rstrip() for line in proc.stdout.splitlines() if line.strip()]
    if len(lines) <= 1:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[1 : limit + 1]:
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        rows.append(
            {
                "pid": int(parts[0]),
                "ppid": int(parts[1]),
                "rss_bytes": int(parts[2]) * 1024,
                "mem_percent": float(parts[3]),
                "cpu_percent": float(parts[4]),
                "elapsed": parts[5],
                "cmd": parts[6],
            }
        )
    return rows


def gpu_summary() -> dict[str, Any] | None:
    if shutil.which("nvidia-smi") is None:
        return None
    proc = run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        ]
    )
    if proc.returncode != 0:
        return None
    rows = list(csv.reader(line for line in proc.stdout.splitlines() if line.strip()))
    if not rows:
        return None
    first = [cell.strip() for cell in rows[0]]
    try:
        return {
            "name": first[0],
            "memory_used_mib": int(first[1]),
            "memory_total_mib": int(first[2]),
            "utilization_percent": int(first[3]),
            "compute_apps": gpu_compute_apps(),
        }
    except (ValueError, IndexError):
        return None


def gpu_compute_apps() -> list[dict[str, Any]]:
    proc = run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    if proc.returncode != 0:
        return []
    apps: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = [cell.strip() for cell in next(csv.reader([line]))]
        if len(parts) < 3:
            continue
        try:
            apps.append(
                {
                    "pid": int(parts[0]),
                    "process_name": parts[1],
                    "used_memory_mib": int(parts[2]),
                }
            )
        except ValueError:
            continue
    return apps


def windows_processes() -> list[dict[str, Any]]:
    proc = run(["tasklist.exe"])
    if proc.returncode != 0:
        return []
    interesting = []
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        if not any(token in lower for token in ("vmmem", "wsl", "llama", "python")):
            continue
        interesting.append({"line": line})
    return interesting


def diagnose(snapshot: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    mem = snapshot.get("wsl_memory")
    if mem:
        used = mem["used"]
        cache = mem["buff_cache"]
        total = mem["total"]
        if cache > used * 4 and cache > 8 * 1024**3:
            notes.append(
                "WSL memory is cache-heavy; high vmmemWSL is likely page/file cache, not active process usage."
            )
        if used < 2 * 1024**3 and cache > 8 * 1024**3:
            notes.append(
                "If Windows RAM pressure matters more than keeping this WSL session alive, run `wsl --shutdown` from PowerShell to reclaim the cache."
            )
        notes.append(
            f"WSL active used memory is {human_bytes(used)} out of {human_bytes(total)}."
        )
    gpu = snapshot.get("gpu")
    if gpu:
        notes.append(
            f"GPU usage is {gpu['memory_used_mib']} MiB / {gpu['memory_total_mib']} MiB at {gpu['utilization_percent']}% utilization."
        )
        if gpu.get("compute_apps"):
            notes.append("GPU compute apps are active.")
    top = snapshot.get("top_wsl_processes") or []
    if top:
        biggest = top[0]
        notes.append(
            f"Largest WSL process is PID {biggest['pid']} using {human_bytes(biggest['rss_bytes'])}: {biggest['cmd'][:120]}"
        )
    return notes


def collect_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "wsl_memory": parse_free_bytes(),
        "top_wsl_processes": top_wsl_processes(),
        "gpu": gpu_summary(),
        "windows_processes": windows_processes(),
    }
    snapshot["diagnosis"] = diagnose(snapshot)
    return snapshot


def print_snapshot(snapshot: dict[str, Any]) -> None:
    print(f"Timestamp: {snapshot['timestamp']}")
    mem = snapshot.get("wsl_memory")
    if mem:
        print("WSL Memory:")
        print(
            "  total={total} used={used} free={free} cache={cache} available={available}".format(
                total=human_bytes(mem["total"]),
                used=human_bytes(mem["used"]),
                free=human_bytes(mem["free"]),
                cache=human_bytes(mem["buff_cache"]),
                available=human_bytes(mem["available"]),
            )
        )
    gpu = snapshot.get("gpu")
    if gpu:
        print("GPU:")
        print(
            f"  {gpu['name']}: {gpu['memory_used_mib']} MiB / {gpu['memory_total_mib']} MiB, util {gpu['utilization_percent']}%"
        )
        for app in gpu.get("compute_apps", []):
            print(
                f"  compute-app pid={app['pid']} mem={app['used_memory_mib']} MiB name={app['process_name']}"
            )
    print("Top WSL processes:")
    for row in snapshot.get("top_wsl_processes", []):
        print(
            f"  pid={row['pid']} rss={human_bytes(row['rss_bytes'])} cpu={row['cpu_percent']}% elapsed={row['elapsed']} cmd={row['cmd'][:140]}"
        )
    print("Windows processes:")
    for row in snapshot.get("windows_processes", []):
        print(f"  {row['line']}")
    if snapshot.get("diagnosis"):
        print("Diagnosis:")
        for note in snapshot["diagnosis"]:
            print(f"  - {note}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot Piper host resource usage from WSL.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of readable text.")
    parser.add_argument("--watch", type=float, default=0.0, help="Repeat every N seconds until interrupted.")
    args = parser.parse_args()

    while True:
        snapshot = collect_snapshot()
        if args.json:
            print(json.dumps(snapshot, indent=2))
        else:
            print_snapshot(snapshot)
        if args.watch <= 0:
            return 0
        print("\n---\n")
        sys.stdout.flush()
        time.sleep(args.watch)


if __name__ == "__main__":
    raise SystemExit(main())
