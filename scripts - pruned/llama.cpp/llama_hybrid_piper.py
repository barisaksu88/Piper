import subprocess
import threading
import time

# === Paths to your models and llama-run.exe ===
LLAMA_RUN_PATH = "./llama-run.exe"
FAST_MODEL = "Meta-Llama-3-8B-Instruct.Q5_K_M.gguf"
SMART_MODEL = "nous-hermes-2-yi-34b.Q5_K_M.gguf"
GPU_LAYERS_FAST = "100"   # or less, tune if you want
GPU_LAYERS_SMART = "100"  # tune if needed
THREADS = "8"             # adjust for your CPU

def call_model(model_path, user_input, gpu_layers):
    cmd = [
        LLAMA_RUN_PATH,
        model_path,
        user_input,
        "--ngl", gpu_layers,
        "--threads", THREADS,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stdout.strip()
    # Strip off your input prompt if echoed
    lines = output.splitlines()
    response_lines = [line for line in lines if user_input.strip() not in line]
    response = "\n".join(response_lines).strip()
    # Remove leading prompt artifacts if any
    response = response.lstrip("<|im_end|>").strip()
    return response

def smart_brain_background(user_input):
    # Called as a thread for big-brain tasks
    answer = call_model(SMART_MODEL, user_input, GPU_LAYERS_SMART)
    print(f"\nPiper: After giving it some thought... {answer}\n")

def should_use_smart_model(prompt):
    # Customize trigger for "big brain" tasks
    # Use !smart prefix or auto-detect based on prompt length/keywords
    if prompt.strip().startswith("!smart"):
        return True
    if len(prompt) > 120:  # Very long questions trigger smart model
        return True
    # Add keywords or complexity triggers if you want
    return False

print("Type your message. Type 'exit' to quit.\n")

while True:
    user_input = input("You: ").strip()
    if user_input.lower() == "exit":
        break

    if should_use_smart_model(user_input):
        # Remove the "!smart" prefix for Hermes if present
        cleaned_input = user_input.replace("!smart", "", 1).strip()
        # Start slow Hermes answer in the background
        threading.Thread(target=smart_brain_background, args=(cleaned_input,), daemon=True).start()
        # Give fast Llama-3-8B answer immediately
        fast_answer = call_model(FAST_MODEL, cleaned_input, GPU_LAYERS_FAST)
        print(f"Piper: {fast_answer}")
        print("Piper: I'm going to think on this further and will get back to you soon...\n")
    else:
        fast_answer = call_model(FAST_MODEL, user_input, GPU_LAYERS_FAST)
        print(f"Piper: {fast_answer}\n")
