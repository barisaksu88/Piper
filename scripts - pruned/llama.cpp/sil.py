import pvporcupine
import pyaudio
import numpy as np

ACCESS_KEY = "wDclRSESsxCVlKocgsDHZCidQYTUx1uwMlj0wcRsCJdCt9OGEHGGZQ=="
WAKEWORD_PATH = r"C:\Piper\llama.cpp\Pipers_en_windows_v3_0_0.ppn"
MIC_DEVICE_INDEX = 1  # Try with 0 or 2 if needed

porcupine = pvporcupine.create(access_key=ACCESS_KEY, keyword_paths=[WAKEWORD_PATH])
pa = pyaudio.PyAudio()
stream = pa.open(rate=porcupine.sample_rate, channels=1, format=pyaudio.paInt16,
                 input=True, frames_per_buffer=porcupine.frame_length,
                 input_device_index=MIC_DEVICE_INDEX)

print("Say your wake word...")
try:
    while True:
        pcm = stream.read(porcupine.frame_length, exception_on_overflow=False)
        pcm = np.frombuffer(pcm, dtype=np.int16)
        result = porcupine.process(pcm)
        if result >= 0:
            print("Wake word detected!")
            break
finally:
    stream.close()
    pa.terminate()
    porcupine.delete()
