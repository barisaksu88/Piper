# Piper

> **Not related to [Piper TTS](https://github.com/rhasspy/piper).** This is a completely separate project.

Piper is a **Windows-first local agent system** built around a Route-Plan-Act-Speak execution loop. It runs entirely on your machine with no cloud dependencies — your LLM, your memory, your tools.

## What it does

- Runs a local LLM (llama.cpp with Qwen 3.5 9B) as the reasoning backend
- Maintains persistent user memory and vector recall (ChromaDB)
- Executes workspace tasks: file operations, code execution, web search
- Generates images via ComfyUI
- Speech-to-text (Faster Whisper) and text-to-speech (Kokoro)
- Web UI desktop interface (React + pywebview window, default)
- DearPyGui fallback interface

## Architecture

Three layers, four LLM roles:

| Layer | Role |
|---|---|
| **Orchestrator** (Director) | Routes intent, manages stage progression |
| **Executor** (Worker) | Runs tools within stage restrictions |
| **Prompt Builder** (Architect) | Assembles context for each LLM call |

LLM roles: **Router** (classify intent), **Planner** (decide actions), **Inspector** (verify outcomes), **Persona** (produce user-facing replies).

Core principle: *execution truthfulness outranks fluent narration*. The system trusts tool results and verified state, never planner/persona narration alone.

## Quick start

```bash
# Clone
git clone https://github.com/barisaksu88/Piper.git
cd Piper

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# You need a llama.cpp server running with a GGUF model
# See docs/ for model configuration details

# Launch
python app.py
```

### Requirements

- Windows 10/11
- Python 3.10+
- A GGUF model served via llama.cpp (default: Qwen 3.5 9B Q6_K)
- Optional: ComfyUI for image generation, a microphone for STT

## Documentation

- [`AGENTS.md`](AGENTS.md) — authoritative architecture doctrine
- [`docs/`](docs/) — design docs, roadmaps, checklists
- [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md) — repo structure and runtime wiring
- [`notes/`](notes/) — coder log, known-good states, known issues

## License

[MIT](LICENSE)
