"""ASR adapter smoke test (no audio) â€” matches your baseline surface."""
import importlib
import sys

ASR_MODULE = "scripts.services.asr.vosk_adapter"
# Your baseline exports these:
ACCEPT_CLASSES = ["ASRSvc", "VoskASRSvc"]
ACCEPT_MODULES = ["vosk"]

try:
    asr = importlib.import_module(ASR_MODULE)
    print(f"âœ“ imported {ASR_MODULE}")
except Exception as e:
    print(f"âœ— import {ASR_MODULE}: {e}")
    sys.exit(1)

classes_present = [n for n in ACCEPT_CLASSES if hasattr(asr, n)]
modules_present = [n for n in ACCEPT_MODULES if hasattr(asr, n)]

if not classes_present and not modules_present:
    exported = sorted([n for n in dir(asr) if not n.startswith("_")])
    print(f"âœ— {ASR_MODULE} did not expose known service classes or `vosk` module.")
    print("  looked for classes:", ", ".join(ACCEPT_CLASSES))
    print("  looked for modules:", ", ".join(ACCEPT_MODULES))
    print("  available:", ", ".join(exported[:40]), ("â€¦" if len(exported) > 40 else ""))
    sys.exit(1)

if classes_present:
    print(f"âœ“ ASR service classes: {', '.join(classes_present)}")
if modules_present:
    print(f"âœ“ Embedded modules: {', '.join(modules_present)}")

print("âœ“ ASR adapter surface looks OK.")


