# C:\Piper\tools\tee_runner.py
import os, sys, subprocess, time
from pathlib import Path

ROOT = Path(r"C:\Piper")
RUN  = ROOT / "run"
LOG  = RUN / "core.log"

def main():
    # UTF-8 hygiene for the child
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    RUN.mkdir(parents=True, exist_ok=True)
    try:
        LOG.unlink()
    except FileNotFoundError:
        pass

    # If the .pth ever went missing, uncomment the next line as a safety net:
    # sys.path.insert(0, str(ROOT / "scripts"))

    # Use the same Python that runs this script (ideally your venv python)
    py = sys.executable
    mod = "scripts.entries.app_cli_entry"

    # Start child with unbuffered text I/O merged to stdout
    p = subprocess.Popen(
        [py, "-u", "-m", mod],
        cwd=str(ROOT),               # important: run at C:\Piper
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,                   # line-buffered
        text=True,                   # decode to str
        encoding="utf-8",
        errors="replace"             # never crash on odd bytes
    )

    # Tee: console + UTF-8 log
    with LOG.open("a", encoding="utf-8", newline="") as f:
        try:
            # Read line-by-line to keep the CLI prompt responsive
            for line in p.stdout:
                # Echo to console exactly as-is
                sys.stdout.write(line)
                sys.stdout.flush()
                # Write to log
                f.write(line)
                f.flush()
        except KeyboardInterrupt:
            # Pass Ctrl+C through to child cleanly
            try:
                p.terminate()
            except Exception:
                pass
        finally:
            # Drain until child exits
            try:
                while True:
                    tail = p.stdout.readline()
                    if not tail:
                        break
                    sys.stdout.write(tail)
                    sys.stdout.flush()
                    f.write(tail)
                    f.flush()
            except Exception:
                pass

    # Propagate child exit code
    p.wait()
    return p.returncode

if __name__ == "__main__":
    sys.exit(main())
