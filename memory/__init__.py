from __future__ import annotations

from .chat_state import ChatState

__all__ = ["ChatState", "KnowledgeManager", "WorldModelManager", "PiperBrain", "get_brain"]


def __getattr__(name: str):
    if name == "KnowledgeManager":
        from .world_model import WorldModelManager as KnowledgeManager

        return KnowledgeManager
    if name == "WorldModelManager":
        from .world_model import WorldModelManager

        return WorldModelManager
    if name in {"PiperBrain", "get_brain"}:
        from .brain import PiperBrain, get_brain

        return {"PiperBrain": PiperBrain, "get_brain": get_brain}[name]
    raise AttributeError(name)
