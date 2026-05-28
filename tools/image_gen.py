"""tools/image_gen.py

Handles Image Generation & Editing via ComfyUI API.
"""

import logging
import subprocess
import time
import json
import urllib.request
import os
import random
import shutil
from pathlib import Path
from config import CFG
from core.runtime_control import CancellationToken, OperationCancelled

# Configuration moved to config.py
COMFY_PORT = 8188
_LOG = logging.getLogger(__name__)

class ImageGenerator:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.workspace = data_dir / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.process = None
        self.last_image_name = None
        # Load path from config
        self.comfy_dir = CFG.COMFY_DIR

    @staticmethod
    def _raise_if_cancelled(cancel_token: CancellationToken | None) -> None:
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()

    def _wait_for_server(self, cancel_token: CancellationToken | None = None):
        _LOG.info("[ImageGen] Waiting for ComfyUI API...")
        start = time.time()
        while time.time() - start < 120:
            self._raise_if_cancelled(cancel_token)
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{COMFY_PORT}/system_stats", method='GET')
                with urllib.request.urlopen(req, timeout=1) as resp:
                    if resp.status == 200:
                        _LOG.info("[ImageGen] ComfyUI Ready.")
                        return True
            except:
                time.sleep(1)
        return False

    def start_server(self):
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{COMFY_PORT}/system_stats", method='GET')
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status == 200:
                    _LOG.info("[ImageGen] ComfyUI already running.")
                    return
        except:
            pass

        _LOG.info("[ImageGen] Starting ComfyUI Silent...")
        python_exe = self.comfy_dir / "python_embeded" / "python.exe"
        if not python_exe.exists(): python_exe = "python"

        cmd = [str(python_exe), str(self.comfy_dir / "ComfyUI" / "main.py"), "--listen", "127.0.0.1", "--port", str(COMFY_PORT), "--disable-metadata"]
        
        try:
            self.process = subprocess.Popen(cmd, cwd=str(self.comfy_dir / "ComfyUI"), creationflags=0x08000000)
        except Exception as e:
            _LOG.error("[ImageGen] Failed to start: %s", e)

    def stop_server(self):
        if self.process:
            _LOG.info("[ImageGen] Stopping ComfyUI...")
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except:
                pass
            self.process = None

        self._kill_processes_on_port(COMFY_PORT)

    def _kill_processes_on_port(self, port: int) -> None:
        """Kill any processes listening on the given port.

        Uses psutil if available, otherwise falls back to platform-specific
        subprocess commands (taskkill on Windows, kill on Unix).
        """
        try:
            import psutil
        except ImportError:  # pragma: no cover
            psutil = None  # type: ignore[assignment]

        pids = set()

        if psutil is not None:
            for conn in psutil.net_connections(kind="inet"):
                if conn.status == psutil.CONN_LISTENING and conn.laddr.port == port:
                    if conn.pid is not None:
                        pids.add(conn.pid)
            for pid in pids:
                try:
                    proc = psutil.Process(pid)
                    proc.kill()
                    _LOG.info("[ImageGen] Killed process %s on port %s", pid, port)
                except Exception:
                    pass
            return

        # Fallback without psutil
        import platform
        if platform.system() == "Windows":
            try:
                result = subprocess.run(
                    ["netstat", "-aon"],
                    capture_output=True, text=True, timeout=10
                )
                for line in result.stdout.splitlines():
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.strip().split()
                        if parts:
                            try:
                                pid = int(parts[-1])
                                pids.add(pid)
                            except ValueError:
                                continue
                for pid in pids:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True, timeout=10
                    )
                    _LOG.info("[ImageGen] Killed process %s on port %s", pid, port)
            except Exception:
                pass
        else:
            try:
                result = subprocess.run(
                    ["lsof", "-i", f":{port}", "-t"],
                    capture_output=True, text=True, timeout=10
                )
                for line in result.stdout.strip().splitlines():
                    try:
                        pids.add(int(line.strip()))
                    except ValueError:
                        continue
                for pid in pids:
                    subprocess.run(
                        ["kill", "-9", str(pid)],
                        capture_output=True, timeout=10
                    )
                    _LOG.info("[ImageGen] Killed process %s on port %s", pid, port)
            except Exception:
                pass

    def generate(self, prompt: str, *, cancel_token: CancellationToken | None = None) -> str:
        """Generates an image using Z-Image (1248x1248)."""

        self._raise_if_cancelled(cancel_token)
        self.start_server()
        if not self._wait_for_server(cancel_token=cancel_token):
            self.stop_server()
            return "Error: ComfyUI did not start."

        # Updated Workflow for Z-Image
        workflow = {
          "9": {"inputs": {"filename_prefix": "PiperGen", "images": ["43", 0]}, "class_type": "SaveImage"},
          "39": {"inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2", "device": "default"}, "class_type": "CLIPLoader"},
          "40": {"inputs": {"vae_name": "ae.safetensors"}, "class_type": "VAELoader"},
          "42": {"inputs": {"conditioning": ["45", 0]}, "class_type": "ConditioningZeroOut"},
          "43": {"inputs": {"samples": ["44", 0], "vae": ["40", 0]}, "class_type": "VAEDecode"},
          "44": {
            "inputs": {
              "seed": random.randint(0, 2**32), "steps": 9, "cfg": 1, "sampler_name": "euler_ancestral",
              "scheduler": "beta", "denoise": 1, "model": ["47", 0], "positive": ["45", 0],
              "negative": ["42", 0], "latent_image": ["58", 0]
            },
            "class_type": "KSampler"
          },
          "45": {"inputs": {"text": prompt, "clip": ["39", 0]}, "class_type": "CLIPTextEncode"},
          "46": {"inputs": {"unet_name": "z_image_turbo_bf16.safetensors", "weight_dtype": "default"}, "class_type": "UNETLoader"},
          "47": {"inputs": {"shift": 3, "model": ["46", 0]}, "class_type": "ModelSamplingAuraFlow"},
          "58": {"inputs": {"width": 1248, "height": 1248, "batch_size": 1}, "class_type": "EmptySD3LatentImage"}
        }

        try:
            self._raise_if_cancelled(cancel_token)
            url = f"http://127.0.0.1:{COMFY_PORT}/prompt"
            data = json.dumps({"prompt": workflow}).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
            
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
            
            prompt_id = result.get('prompt_id')
            _LOG.info("[ImageGen] Job queued (%s). Monitoring...", prompt_id)
            
            fname = self._poll_for_result(prompt_id, cancel_token=cancel_token)
            if fname:
                self.last_image_name = fname 
                return f"Image saved to: {fname}"
            return "Error: Generation failed or timed out."

        except OperationCancelled:
            raise
        except Exception as e:
            return f"Error: {e}"
        finally:
            self.stop_server()

    def edit_image(self, instruction: str, *, cancel_token: CancellationToken | None = None) -> str:
        """Edits the last generated image using Qwen Image Edit."""

        self._raise_if_cancelled(cancel_token)
        if not self.last_image_name:
            return "Error: No image has been generated yet to edit."

        src_path = self.workspace / self.last_image_name
        comfy_out = self.comfy_dir / "ComfyUI" / "output"
        
        if not src_path.exists():
            comfy_src = comfy_out / self.last_image_name
            if comfy_src.exists():
                shutil.copy(comfy_src, src_path)
            else:
                return f"Error: Source image {self.last_image_name} not found."

        input_path = self.comfy_dir / "ComfyUI" / "input" / self.last_image_name
        shutil.copy(src_path, input_path)
        _LOG.info("[ImageGen] Prepared input image: %s", self.last_image_name)

        self.start_server()
        if not self._wait_for_server(cancel_token=cancel_token):
            self.stop_server()
            return "Error: ComfyUI did not start."

        # Qwen Image Edit Workflow
        workflow = {
          "60": {"inputs": {"filename_prefix": "PiperEdit", "images": ["102:8", 0]}, "class_type": "SaveImage"},
          "78": {"inputs": {"image": self.last_image_name}, "class_type": "LoadImage"}, 
          "93": {"inputs": {"upscale_method": "lanczos", "megapixels": 1.5, "resolution_steps": 1, "image": ["78", 0]}, "class_type": "ImageScaleToTotalPixels"},
          "102:77": {"inputs": {"prompt": "", "clip": ["102:38", 0], "vae": ["102:39", 0], "image": ["93", 0]}, "class_type": "TextEncodeQwenImageEdit"},
          "102:75": {"inputs": {"strength": 1, "model": ["102:66", 0]}, "class_type": "CFGNorm"},
          "102:66": {"inputs": {"shift": 3, "model": ["102:89", 0]}, "class_type": "ModelSamplingAuraFlow"},
          "102:8": {"inputs": {"samples": ["102:3", 0], "vae": ["102:39", 0]}, "class_type": "VAEDecode"},
          "102:88": {"inputs": {"pixels": ["93", 0], "vae": ["102:39", 0]}, "class_type": "VAEEncode"},
          "102:39": {"inputs": {"vae_name": "qwen_image_vae.safetensors"}, "class_type": "VAELoader"},
          "102:102": {"inputs": {"unet_name": "Qwen_Image_Edit-Q3_K_M.gguf"}, "class_type": "UnetLoaderGGUF"},
          "102:89": {"inputs": {"lora_name": "Qwen-Image-Lightning-4steps-V1.0.safetensors", "strength_model": 1, "model": ["102:102", 0]}, "class_type": "LoraLoaderModelOnly"},
          "102:38": {"inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}, "class_type": "CLIPLoader"},
          "102:76": {"inputs": {"prompt": instruction, "clip": ["102:38", 0], "vae": ["102:39", 0], "image": ["93", 0]}, "class_type": "TextEncodeQwenImageEdit"},
          "102:3": {
            "inputs": {
              "seed": random.randint(0, 2**32), "steps": 4, "cfg": 1, "sampler_name": "euler_ancestral",
              "scheduler": "beta", "denoise": 1, "model": ["102:75", 0], "positive": ["102:76", 0],
              "negative": ["102:77", 0], "latent_image": ["102:88", 0]
            },
            "class_type": "KSampler"
          }
        }

        try:
            self._raise_if_cancelled(cancel_token)
            url = f"http://127.0.0.1:{COMFY_PORT}/prompt"
            data = json.dumps({"prompt": workflow}).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
            
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
            
            prompt_id = result.get('prompt_id')
            _LOG.info("[ImageGen] Edit Job queued (%s). Monitoring...", prompt_id)
            
            fname = self._poll_for_result(prompt_id, prefix="PiperEdit", cancel_token=cancel_token)
            if fname:
                self.last_image_name = fname 
                return f"Edited image saved to: {fname}"
            return "Error: Edit failed or timed out."

        except OperationCancelled:
            raise
        except Exception as e:
            return f"Error: {e}"
        finally:
            self.stop_server()

    def _poll_for_result(self, prompt_id, prefix="PiperGen", timeout=180, cancel_token: CancellationToken | None = None):
        """Helper to poll history for result."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            self._raise_if_cancelled(cancel_token)
            time.sleep(2)
            try:
                hist_req = urllib.request.Request(f"http://127.0.0.1:{COMFY_PORT}/history/{prompt_id}")
                with urllib.request.urlopen(hist_req, timeout=5) as resp:
                    self._raise_if_cancelled(cancel_token)
                    history = json.loads(resp.read())
                    
                if prompt_id in history:
                    status = history[prompt_id].get('status', {})
                    if status.get('completed', False):
                        outputs = history[prompt_id].get('outputs', {})
                        for node_id, out_data in outputs.items():
                            if 'images' in out_data:
                                imgs = out_data['images']
                                if imgs:
                                    filename = imgs[0]['filename']
                                    subfolder = imgs[0].get('subfolder', '')
                                    
                                    comfy_output = self.comfy_dir / "ComfyUI" / "output"
                                    src = comfy_output / subfolder / filename if subfolder else comfy_output / filename
                                    
                                    if src.exists():
                                        dest = self.workspace / filename
                                        dest.write_bytes(src.read_bytes())
                                        return filename
                    if status.get('status_str') == 'error':
                        return None
            except OperationCancelled:
                raise
            except:
                pass
        return None
