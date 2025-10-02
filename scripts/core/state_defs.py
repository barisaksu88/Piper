# Ring-0 Core: enum names only (no behavior). Safe to import.
from enum import Enum, auto

class CoreState(Enum):
    SLEEPING = auto()
    WAKING = auto()
    LISTENING = auto()
    THINKING = auto()
    SPEAKING = auto()

class EventType(Enum):
    WakeDetected = auto()
    ASRResult = auto()
    Speak = auto()
    StopSpeak = auto()
    Sleep = auto()

__all__ = ["CoreState", "EventType"]

