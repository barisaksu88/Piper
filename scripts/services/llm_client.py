# CONTRACT — Client (Unified)
# - generate(...) and stream_generate(...): build prompt via ChatML Jinja.
# - Persona from persona_source; recent-turns only; NO recall/capsule.
# - Bind provider args (model path, context_size, threads, ngl, temps) from config.
# - Log rendered prompt (truncated) to prompt_log.
# - Cancel/stop preserved from I01. No env reads.

import io
import os
import time
from typing import Dict, Any, List, Iterator, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from .config_loader import load_config, print_startup_summary
from .persona_source import load_persona
from .providers.llamacpp import LlamaCppProcess

TRUNC_LINE = 16_000

class LLMClient:
    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = cfg or load_config()
        print_startup_summary(self.cfg)

        p = self.cfg["prompt"]
        tmpl_path = os.path.dirname(p["template"])
        tmpl_name = os.path.basename(p["template"])

        self._jinja = Environment(
            loader=FileSystemLoader(tmpl_path),
            autoescape=select_autoescape(enabled_extensions=("jinja",)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._template = self._jinja.get_template(tmpl_name)

        self._provider_proc: Optional[LlamaCppProcess] = None

    # --- internal helpers ---

    def _render_prompt(self, messages: List[Dict[str, str]]) -> str:
        persona_files = self.cfg["persona"]
        persona = load_persona(
            persona_files["background_file"],
            persona_files["traits_file"],
            legacy_yaml=False if not self.cfg.get("features", {}).get("use_legacy_envs") else False,
        )
        reply_budget = self.cfg["prompt"]["reply_reserve_tokens"]

        rendered = self._template.render(
            background=persona["background"],
            traits=persona["traits"],
            messages=messages,
            reply_budget_tokens=reply_budget,
        )
        self._append_prompt_log(rendered)
        return rendered

    def _append_prompt_log(self, text: str) -> None:
        log_path = self.cfg["paths"]["prompt_log"]
        # append with truncation per line
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8", newline="\n") as f:
            f.write("\n--- turn @ %s ---\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            for line in text.splitlines():
                if len(line) > TRUNC_LINE:
                    f.write(line[:TRUNC_LINE] + " …[truncated]\n")
                else:
                    f.write(line + "\n")

    def _make_provider(self, prompt: str) -> LlamaCppProcess:
        m = self.cfg["model"]
        pv = self.cfg.get("provider", {})
        exe = pv.get("llamacpp_exe") or r"C:\Piper\llama.cpp\llama-run"
        return LlamaCppProcess(
            exe=exe,
            model_path=m["path"],
            ctx=m["context_size"],
            threads=m["threads"],
            ngl=m["ngl"],
            temperature=m["temperature"],
            top_k=m["top_k"],
            top_p=m["top_p"],
            extra_args=[]
        )

    # --- public API ---

    def generate(self, messages: List[Dict[str, str]]) -> str:
        """
        Synchronous convenience. Prefer stream_generate() in UI.
        """
        prompt = self._render_prompt(messages)
        proc = self._make_provider(prompt)
        proc.start(prompt)
        out_chunks = []
        for chunk in proc.stream():
            out_chunks.append(chunk)
        proc.stop()
        return "".join(out_chunks)

    def stream_generate(self, messages: List[Dict[str, str]]) -> Iterator[str]:
        """
        Streaming iterator. UI hook should fold chunks into a single assistant turn.
        """
        prompt = self._render_prompt(messages)
        self._provider_proc = self._make_provider(prompt)
        self._provider_proc.start(prompt)
        try:
            for chunk in self._provider_proc.stream():
                yield chunk
        finally:
            self.stop()

    def stop(self):
        if self._provider_proc:
            self._provider_proc.stop()
            self._provider_proc = None
