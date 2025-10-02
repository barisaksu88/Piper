import sys
import pyttsx3

VOICE_INDEX = 1  # Use Hazel British Female

def main():
    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Hello, this is Hazel."
    engine = pyttsx3.init()
    voices = engine.getProperty('voices')
    engine.setProperty('voice', voices[VOICE_INDEX].id)
    engine.setProperty('rate', 205)  # Optional: Adjust speaking speed
    engine.say(text)
    engine.runAndWait()

if __name__ == "__main__":
    main()
