import pvporcupine
import pyaudio
import sounddevice as sd
import queue
import webrtcvad
import time
import numpy as np
import vosk
import threading
import sys
import subprocess

# === CONFIG ===
MIC_DEVICE_INDEX = 1
CHANNELS = 1
SAMPLE_RATE = 16000
VOSK_MODEL_PATH = r"C:\Piper\llama.cpp\vosk-model-small-en-us-0.15"
WAKEWORD = 'piper'
SESSION_TIMEOUT = 60      # 1 minute in seconds
SILENCE_TIMEOUT = 2.0     # 2 seconds after speech ends = "done speaking"
LLAMA_RUN = "./llama-run.exe"  # or wherever your llama-run.exe is
MODEL_PATH = "Hermes-2-Pro-Llama-3-8B-Q5_K_M.gguf"
GPU_LAYERS = "100"
THREADS = "8"

def clean_output(raw_output):
    for code in ["←[0m", "<|im_end|>", "<|im_start|>", "\x1b[0m"]:
        raw_output = raw_output.replace(code, "")
    cleaned = raw_output.strip()
    # (You can add any other output cleaning here)
    return cleaned

def get_model_response(prompt):
    cmd = [
        LLAMA_RUN,
        MODEL_PATH,
        "--ngl", GPU_LAYERS,
        "--threads", THREADS,
        "--n-predict", "1000",   # Increase if you get cutoff
        "--temp", "0.7",
        "--top-p", "0.95",
        "--top-k", "25",
        "--repeat-penalty", "1.18"
    ]
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8")
    output = clean_output(result.stdout)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        if line and not line.lower().startswith("user:") and not line.lower().startswith("piper:"):
            return line
    return ""


# === PIPER RESPONSE (replace with your own function if you wish) ===
def get_piper_reply(user_text):
    return get_model_response(user_text)


def speak(text):
    # Replace with your TTS logic (calls your speak_once.py, etc)
    subprocess.Popen([sys.executable, "speak_once.py", text])

# === SMART VOICE LISTENING ===
def listen_until_silence():
    vad = webrtcvad.Vad(1)  # 0=most aggressive, 3=most sensitive
    vosk_model = vosk.Model(VOSK_MODEL_PATH)
    recognizer = vosk.KaldiRecognizer(vosk_model, SAMPLE_RATE)
    q = queue.Queue()

    def callback(indata, frames, time_, status):
        q.put(bytes(indata))

    with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=320, device=MIC_DEVICE_INDEX, dtype='int16',
                           channels=CHANNELS, callback=callback):
        print("Listening for your command...")
        audio_chunks = []
        last_voice_time = time.time()
        speaking = False
        start_time = time.time()
        transcript = ""
        while True:
            if time.time() - start_time > SESSION_TIMEOUT:
                print("Session timed out (1 minute).")
                break
            data = q.get()
            if vad.is_speech(data, SAMPLE_RATE):
                last_voice_time = time.time()
                speaking = True
                audio_chunks.append(data)
            elif speaking and (time.time() - last_voice_time > SILENCE_TIMEOUT):
                # User stopped talking for SILENCE_TIMEOUT
                break
            # Feed all data to recognizer for incremental transcript
            if recognizer.AcceptWaveform(data):
                res = recognizer.Result()
                text = eval(res).get("text", "")
                if text:
                    transcript += " " + text
                    # Sleep command?
                    if "piper sleep" in text.lower():
                        print("Heard 'piper sleep', session ending.")
                        return "piper sleep"
        # Finalize transcript
        if recognizer.AcceptWaveform(b""):
            res = recognizer.FinalResult()
            text = eval(res).get("text", "")
            if text:
                transcript += " " + text
        return transcript.strip()

# === WAKE WORD LISTENING ===
def wait_for_wake_word():
    print("Piper is sleeping. Say your custom wake word to wake her.")
    ACCESS_KEY = "wDclRSESsxCVlKocgsDHZCidQYTUx1uwMlj0wcRsCJdCt9OGEHGGZQ=="
    WAKEWORD_PATH = r"C:\Piper\llama.cpp\Pipers_en_windows_v3_0_0.ppn"
    porcupine = pvporcupine.create(access_key=ACCESS_KEY, keyword_paths=[WAKEWORD_PATH])

    pa = pyaudio.PyAudio()
    audio_stream = pa.open(
        rate=porcupine.sample_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=porcupine.frame_length,
        input_device_index=MIC_DEVICE_INDEX
    )
    try:
        while True:
            pcm = audio_stream.read(porcupine.frame_length, exception_on_overflow=False)
            pcm = np.frombuffer(pcm, dtype=np.int16)
            result = porcupine.process(pcm)
            if result >= 0:
                print("Wake word detected!")
                break
    finally:
        audio_stream.close()
        pa.terminate()
        porcupine.delete()

# === MAIN LOOP ===
def main():
    while True:
        wait_for_wake_word()
        while True:
            user_text = listen_until_silence()
            user_text = user_text.strip()
            if not user_text:
                print("No input detected, Piper is going back to sleep.")
                break
            if "piper sleep" in user_text.lower():
                print("Piper going back to sleep.")
                break
            print(f"You: {user_text}")
            response = get_piper_reply(user_text)
            print(f"Piper: {response}")

            # === AUTO-MUTE: Block Until TTS is Done ===
            tts_proc = subprocess.Popen([sys.executable, "speak_once.py", response])
            tts_proc.wait()  # Waits for TTS to finish

            # (NOW it goes back to listening for your next command)


if __name__ == "__main__":
    main()
