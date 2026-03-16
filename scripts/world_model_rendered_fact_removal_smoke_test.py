from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from memory import KnowledgeManager  # noqa: E402
from memory.state_owner import SharedStateOwner  # noqa: E402


@dataclass(frozen=True)
class WorldModelRenderedFactRemovalSmokeReport:
    success: bool
    before_contains_rendered_relation: bool
    remove_result: bool
    after_contains_rendered_relation: bool
    after_contains_project_entity: bool


def run_smoke() -> WorldModelRenderedFactRemovalSmokeReport:
    with tempfile.TemporaryDirectory(prefix="piper-world-model-remove-") as tmp:
        data_dir = Path(tmp)
        owner = SharedStateOwner.for_data_dir(data_dir)
        manager = KnowledgeManager(
            data_dir,
            llm_client=None,
            world_model_store=owner.world_model_store,
            knowledge_store=owner.knowledge_store,
        )

        owner.world_model_store.save_graph(
            {
                "schema_version": 1,
                "root_entity_id": "person:user",
                "nodes": {
                    "person:user": {
                        "id": "person:user",
                        "type": "person",
                        "label": "Baris",
                        "aliases": ["Baris", "user", "me"],
                        "attributes": {},
                        "updated_at": 1773435811,
                    },
                    "project:catch-the-stars": {
                        "id": "project:catch-the-stars",
                        "type": "project",
                        "label": "Catch the Stars",
                        "aliases": ["Catch the Stars"],
                        "attributes": {
                            "file_name": [
                                {
                                    "value": "catch_the_stars.py",
                                    "expires_at": 1774439229,
                                    "updated_at": 1773435811,
                                }
                            ]
                        },
                        "updated_at": 1773435811,
                    },
                },
                "edges": [
                    {
                        "source": "person:user",
                        "relation": "works_on",
                        "target": "project:catch-the-stars",
                        "expires_at": None,
                        "updated_at": 1773435811,
                    }
                ],
                "metadata": {
                    "created_at": 1773435811,
                    "updated_at": 1773435811,
                    "migrated_from_legacy_knowledge": False,
                },
            }
        )

        before = manager.list_for_display()
        remove_result = bool(manager.remove_fact("works on: Catch the Stars"))
        after = manager.list_for_display()

        report = WorldModelRenderedFactRemovalSmokeReport(
            success=(
                "- works on: Catch the Stars" in before
                and remove_result
                and "- works on: Catch the Stars" not in after
                and "Entity: Catch the Stars (project)" not in after
            ),
            before_contains_rendered_relation="- works on: Catch the Stars" in before,
            remove_result=remove_result,
            after_contains_rendered_relation="- works on: Catch the Stars" in after,
            after_contains_project_entity="Entity: Catch the Stars (project)" in after,
        )
        return report


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
