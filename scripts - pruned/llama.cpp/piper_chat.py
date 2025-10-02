import subprocess
import os
import datetime

MODEL = "mythomax-l2-13b.Q5_K_M.gguf"
MEMORY_FILE = "piper_memory.txt"
MAX_HISTORY_CHARS = 6000  # Max context to send each time (adjust as you like!)

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def save_memory(history):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        f.write(history)

def main():
    print("Piper is ready. Type 'exit' to quit.\n")
    history = load_memory()
    while True:
        user = input("You: ").strip()
        if user.lower() == "exit":
            break

        # Append user message to history, trimming if necessary
        history += f"\nUser: {user}\nPiper:"
        short_history = history[-MAX_HISTORY_CHARS:]

        # Run llama.cpp with full context, get output (change ./llama-run to .\llama-run.exe if needed on Windows)
        result = subprocess.run(
            [
                "./llama-run",
                MODEL,
                "--temp", "0.8",
                "--n-predict", "200",
                "--repeat-penalty", "1.1",
                "--threads", "8",
                short_history
            ],
            capture_output=True,
            text=True
        )

        # Extract and print Piper's reply
        reply = result.stdout.strip().split("Piper:")[-1].strip()
        print(f"Piper: {reply}\n")

        # Save the new exchange in memory
        history += reply
        save_memory(history)

if __name__ == "__main__":
    main()
