# C:\Piper\mission_control\mission_control.py
# Mission Control v1.8 — Stop GUI fixed using taskkill (Windows-native)

from __future__ import annotations
import os, sys, time, subprocess, threading
from pathlib import Path
import dearpygui.dearpygui as dpg
import pyperclip

ROOT = Path(r"C:\Piper")
MC_DIR = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
RUN_DIR = ROOT / "run"
SNAPSHOTS = ROOT / "snapshots"
LOG_FILE = RUN_DIR / "core.log"

processes = {"CLI": None, "GUI": None, "WatchdogA": None, "WatchdogB": None}

# -----------------------------------------------------------------------------
# Process management
# -----------------------------------------------------------------------------
def run_py(name: str, py_file: Path, with_stdin=False):
    proc = subprocess.Popen(
        [sys.executable, str(py_file)],
        cwd=str(MC_DIR),
        creationflags=subprocess.CREATE_NEW_CONSOLE if not with_stdin else 0,
        stdin=subprocess.PIPE if with_stdin else None,
        text=True
    )
    processes[name] = proc
    return proc

# -----------------------------------------------------------------------------
# CLI special: start hidden with stdin pipe
# -----------------------------------------------------------------------------
def start_cli_bg():
    if processes["CLI"] and processes["CLI"].poll() is None:
        return processes["CLI"]
    cli_py = MC_DIR / "launch_cli.py"
    return run_py("CLI", cli_py, with_stdin=True)

def send_cli_command(sender, app_data):
    line = app_data.strip()
    if not line:
        return
    proc = processes.get("CLI")
    if not proc or proc.poll() is not None:
        proc = start_cli_bg()
    try:
        proc.stdin.write(line + "\n")
        proc.stdin.flush()
    except Exception:
        pass
    dpg.set_value("cli_input", "")
    dpg.focus_item("cli_input")  # re-focus input box

# -----------------------------------------------------------------------------
# Snapshot
# -----------------------------------------------------------------------------
def make_snapshot():
    run_py("Snapshot", MC_DIR / "launch_snapshot.py")

def list_snapshots():
    files = sorted(SNAPSHOTS.glob("*.zip"), key=os.path.getmtime, reverse=True)[:5]
    return [f.name for f in files]

# -----------------------------------------------------------------------------
# Logs tailer
# -----------------------------------------------------------------------------
def tail_logs():
    last_size = 0
    while True:
        try:
            if LOG_FILE.exists():
                size = LOG_FILE.stat().st_size
                if size > last_size:
                    with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(last_size)
                        new = f.read()
                        if new:
                            dpg.set_value("logs_text", dpg.get_value("logs_text") + new)
                    last_size = size
        except Exception:
            pass
        time.sleep(1)

# -----------------------------------------------------------------------------
# GUI
# -----------------------------------------------------------------------------

def start_gui_direct():
    # Launch the actual GUI (pythonw.exe -m entries.app_gui_entry) and track its PID
    if processes["GUI"] and processes["GUI"].poll() is None:
        return
    pyw = ROOT / "venv" / "Scripts" / "pythonw.exe"
    proc = subprocess.Popen([str(pyw), "-m", "entries.app_gui_entry"], cwd=str(SCRIPTS))
    processes["GUI"] = proc

def stop_gui():
    proc = processes.get("GUI")
    killed = False
    if proc and proc.poll() is None:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            killed = True
        except Exception:
            try:
                proc.kill(); killed = True
            except Exception:
                pass
        processes["GUI"] = None

    if not killed:
        # Fallback: find any python[w].exe whose CommandLine contains entries.app_gui_entry
        ps = (
            'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '
            '"Get-CimInstance Win32_Process | Where-Object { '
            '$_.Name -match \\"python(w)?\\\\.exe\\" -and $_.CommandLine -match \\"entries\\\\.app_gui_entry\\" } | '
            'ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"'
        )
        subprocess.run(ps, shell=True)

def build_gui():
    with dpg.window(label="Mission Control", width=800, height=600):
        with dpg.tab_bar():
            with dpg.tab(label="Processes"):
                dpg.add_button(label="Start GUI", callback=start_gui_direct)
                dpg.add_button(label="Stop GUI", callback=stop_gui)
                dpg.add_spacer(height=10)
                dpg.add_button(label="Start Watchdog A", callback=lambda: run_py("WatchdogA", MC_DIR / "launch_watchdog_a.py"))
                dpg.add_button(label="Stop Watchdog A", callback=lambda: processes["WatchdogA"].terminate() if processes["WatchdogA"] else None)
                dpg.add_button(label="Start Watchdog B", callback=lambda: run_py("WatchdogB", MC_DIR / "launch_watchdog_b.py"))
                dpg.add_button(label="Stop Watchdog B", callback=lambda: processes["WatchdogB"].terminate() if processes["WatchdogB"] else None)
                dpg.add_spacer(height=10)
                dpg.add_text("CLI Command Input:")
                dpg.add_input_text(tag="cli_input", width=400, callback=send_cli_command, on_enter=True)
                dpg.focus_item("cli_input")  # auto-focus when GUI builds

            with dpg.tab(label="Logs"):
                dpg.add_input_text(multiline=True, readonly=True, tag="logs_text",
                                   width=750, height=400, default_value="")

            with dpg.tab(label="Snapshots"):
                dpg.add_text("Clipboard name will be used for snapshot.")
                dpg.add_button(label="Make Snapshot", callback=make_snapshot)
                dpg.add_text("", tag="snapshot_status")
                dpg.add_spacer(height=10)
                dpg.add_text("Recent Snapshots:")
                dpg.add_listbox(list_snapshots(), width=400, num_items=5, tag="snap_list")

            with dpg.tab(label="Config"):
                dpg.add_text(f"Python: {sys.executable}")
                dpg.add_text(f"Venv: {os.environ.get('VIRTUAL_ENV','(none)')}")
                dpg.add_spacer(height=10)
                dpg.add_button(label="Set Theme=blue",
                               callback=lambda: os.environ.update(PIPER_UI_THEME="blue"))
                dpg.add_button(label="Set Theme=clean",
                               callback=lambda: os.environ.update(PIPER_UI_THEME="clean"))
                dpg.add_button(label="Enable Dev Input",
                               callback=lambda: os.environ.update(PIPER_UI_DEV_INPUT="1"))
                dpg.add_button(label="Disable Dev Input",
                               callback=lambda: os.environ.pop("PIPER_UI_DEV_INPUT", None))

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    dpg.create_context()
    build_gui()
    threading.Thread(target=tail_logs, daemon=True).start()
    dpg.create_viewport(title="Piper Mission Control", width=820, height=640)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.start_dearpygui()
    dpg.destroy_context()
