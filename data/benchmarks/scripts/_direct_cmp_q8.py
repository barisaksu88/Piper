import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import benchmark_models as bm

SERVER = ROOT / 'runtime' / 'llama.cpp' / 'llama-server.exe'
MODELS = {
    'q25': Path(r'C:\Piper\models\llama\qwen2.5-14b-instruct-q4_k_m-00001-of-00003.gguf'),
    'q6': ROOT / 'models' / 'llama' / 'Qwen3.5-4B-Q6_K.gguf',
    'q8': ROOT / 'models' / 'llama' / 'Qwen3.5-4B-Q8_0.gguf',
}
RESULT = ROOT / 'data' / 'benchmarks' / 'results' / 'direct_cmp_q8.json'
PORT_BASE = 8161
CTX = 8192
GPU = 99

def wait_health(base, timeout_s, proc):
    started = time.perf_counter()
    while time.perf_counter() - started < timeout_s:
        if proc.poll() is not None:
            return False, time.perf_counter() - started, f'crashed:{proc.returncode}'
        try:
            req = urllib.request.Request(f'{base}/health', method='GET')
            with urllib.request.urlopen(req, timeout=2) as r:
                if r.status == 200:
                    return True, time.perf_counter() - started, 'ready'
        except urllib.error.HTTPError as exc:
            if exc.code != 503:
                return False, time.perf_counter() - started, f'http:{exc.code}'
        except Exception:
            pass
        time.sleep(1)
    return False, time.perf_counter() - started, 'timeout'

def kill(proc):
    if not proc:
        return
    try:
        proc.terminate()
        proc.wait(timeout=15)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

all_results = []
for idx, (label, model_path) in enumerate(MODELS.items()):
    mmproj = bm.resolve_mmproj(Path(model_path))
    port = PORT_BASE + idx
    base = f'http://127.0.0.1:{port}'
    log_path = ROOT / 'data' / 'benchmarks' / 'logs' / f'{Path(model_path).stem}.q8direct.log'
    cmd = [
        str(SERVER), '-m', str(model_path), '--port', str(port), '--ctx-size', str(CTX), '-ngl', str(GPU), '--host', '127.0.0.1'
    ]
    if mmproj:
        cmd.extend(['--mmproj', str(mmproj)])
    cmd.extend(['--reasoning-budget', str(bm.reasoning_budget_for_model(Path(model_path)))])
    proc = None
    result = {'label': label, 'model_path': str(model_path), 'mmproj_path': str(mmproj) if mmproj else None}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('w', encoding='utf-8', errors='replace') as fh:
        try:
            proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True)
            ok, boot_s, status = wait_health(base, 180.0, proc)
            result.update({'boot_ok': ok, 'boot_seconds': round(boot_s, 3), 'boot_status': status, 'cases': []})
            if ok:
                for case in bm.BENCHMARK_CASES:
                    result['cases'].append(bm.benchmark_case(base, case))
        finally:
            kill(proc)
    all_results.append(result)

RESULT.write_text(json.dumps({'results': all_results}, indent=2), encoding='utf-8')
print(str(RESULT))
