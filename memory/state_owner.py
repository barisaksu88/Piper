from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import data_state_path
from memory.stores import (
    EventStore,
    IntentStateStore,
    KnowledgeStore,
    SituationalStateStore,
    TaskStore,
    WorldModelStore,
)


@dataclass(frozen=True)
class SharedStateOwner:
    data_dir: Path
    task_store: TaskStore
    event_store: EventStore
    knowledge_store: KnowledgeStore
    world_model_store: WorldModelStore
    situational_state_store: SituationalStateStore
    intent_state_store: IntentStateStore

    @classmethod
    def for_data_dir(cls, data_dir: Path) -> "SharedStateOwner":
        base = Path(data_dir)
        return cls(
            data_dir=base,
            task_store=TaskStore(data_state_path(base, "tasks.json")),
            event_store=EventStore(data_state_path(base, "events.json")),
            knowledge_store=KnowledgeStore(data_state_path(base, "knowledge.json")),
            world_model_store=WorldModelStore(data_state_path(base, "world_model.json")),
            situational_state_store=SituationalStateStore(data_state_path(base, "situational_state.json")),
            intent_state_store=IntentStateStore(data_state_path(base, "intent_state.json")),
        )
