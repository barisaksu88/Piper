# C:\Piper\set_env_defaults.ps1
# Reset Piper environment variables to sane defaults
# Run with:  .\set_env_defaults.ps1

Write-Host "[Piper] Applying default environment..."

# --- Provider / Core ---
$env:PIPER_LLM_PROVIDER = "echo"      # default backend
$env:PIPER_LLM_TIMEOUT_MS = "5000"    # 5s sliding timeout

# --- Persona / Style ---
Remove-Item Env:PIPER_PERSONA_TONE -ErrorAction SilentlyContinue
$env:PIPER_PERSONA_SARCASM = "0"      # sarcasm off by default

# --- Memory ---
$env:PIPER_MEM_EPISODES = "C:\Piper\logs\memory_episodes.jsonl"
$env:PIPER_MEM_SUMMARY_TURNS = "6"
$env:PIPER_MEM_SUMMARY_MAX = "200"
$env:PIPER_MEM_MINLEN = "50"
$env:PIPER_MEM_WRITE = "1"            # enable episodic writes
$env:PIPER_MEM_AVG_CHARS_PER_TOKEN = "4"
$env:PIPER_MEM_RECALL = "0"           # recall disabled by default
$env:PIPER_MEM_BUDGET_TOKENS = "256"

# --- UI / Mission Control ---
$env:PIPER_CORE_LOG = "C:\Piper\logs\chat_state.jsonl"
$env:PIPER_UI_TAIL_FROM_START = "0"
$env:PIPER_UI_POLL_SEC = "1"
$env:PIPER_GUI_DEBUG_CHAT = "0"

Write-Host "[Piper] Defaults applied."
