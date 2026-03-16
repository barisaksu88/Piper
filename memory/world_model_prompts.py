from __future__ import annotations

import json
from typing import Any, Dict, List


def _graph_digest(current_graph: Dict[str, Any]) -> str:
    nodes = current_graph.get("nodes") or {}
    edges = current_graph.get("edges") or []
    payload = {
        "root_entity_id": current_graph.get("root_entity_id") or "person:user",
        "nodes": [],
        "relationships": [],
    }

    for node in list(nodes.values())[:12]:
        if not isinstance(node, dict):
            continue
        attributes = {}
        raw_attributes = node.get("attributes") or {}
        if isinstance(raw_attributes, dict):
            for name, entries in raw_attributes.items():
                values = []
                if isinstance(entries, list):
                    for entry in entries[:3]:
                        if not isinstance(entry, dict):
                            continue
                        value = str(entry.get("value") or "").strip()
                        if value:
                            values.append(value)
                if values:
                    attributes[str(name)] = values
        payload["nodes"].append(
            {
                "id": str(node.get("id") or ""),
                "type": str(node.get("type") or "entity"),
                "label": str(node.get("label") or ""),
                "aliases": list(node.get("aliases") or [])[:5],
                "attributes": attributes,
            }
        )

    for edge in edges[:20]:
        if not isinstance(edge, dict):
            continue
        payload["relationships"].append(
            {
                "source": str(edge.get("source") or ""),
                "relation": str(edge.get("relation") or ""),
                "target": str(edge.get("target") or ""),
            }
        )

    return json.dumps(payload, indent=2, ensure_ascii=False)


def build_world_model_extraction_prompt(current_graph: Dict[str, Any], history: List[Dict[str, Any]]) -> str:
    history_slice = history[-8:]
    history_text = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}"
        for m in history_slice
        if m.get("content")
    )
    graph_json = _graph_digest(current_graph)

    return f"""You are Piper's World Model Archivist.

You maintain a graph memory about the user's life.
The source of truth is world_model.json.

Store:
- stable people, projects, places, devices, and recurring preferences
- relationships between entities
- durable attributes on those entities

Do not store:
- temporary requests
- one-off reminders, deadlines, or appointments that belong in tasks/events
- assistant guesses
- generic summaries of the conversation

The user/root entity must always use id `person:user`.

Prefer relationships over stuffing everything into one text blob.
Examples:
- child -> Dora
- partner -> Ekin
- works_on -> Piper
- role/occupation attributes on the relevant entity

When updating attributes:
- use `set` for singular/corrective fields such as name, birth_date, gender, location
- use `add` for coexistence fields such as occupation, interests, vehicles, projects
- use `remove` only if the recent chat explicitly removes or negates a fact

When updating relationships:
- use `set` when the relationship is singular for that source in context
- use `add` when multiple can coexist
- use `remove` only if the user explicitly negated it

If an entity already exists in CURRENT WORLD MODEL, reuse its id.
If you need a new id, use a predictable lowercase slug like:
- person:dora
- project:piper
- place:bostanci
- If two different people share the same human name, keep the same label but use distinct ids such as `person:ekin_partner` and `person:ekin_friend`.
- If the memory is only about a temporary workspace artifact or generated file, keep it temporary with a short ttl such as `14d` instead of `forever`.

OUTPUT FORMAT:
{{
  "entities": [
    {{
      "id": "person:user",
      "type": "person",
      "label": "Baris",
      "aliases": ["baris"],
      "attributes": [
        {{"name": "occupation", "value": "airline pilot", "mode": "add", "ttl": "forever"}}
      ]
    }}
  ],
  "relationships": [
    {{
      "source": "person:user",
      "relation": "child",
      "target": {{"id": "person:dora", "type": "person", "label": "Dora"}},
      "mode": "add",
      "ttl": "forever"
    }}
  ]
}}

STRICT RULES:
- Return only changes grounded in the recent chat.
- Do not restate unchanged parts of the graph.
- Do not invent hidden entities, jobs, dates, or motivations.
- Do not store corrections about assistant mistakes, routing, filenames, script names, or which temporary file/game/tool was being discussed.
- Do not create meta attributes about confusion, clarification, correction, requests, or conversation state.
- Never output code-like values, boolean expressions, assignments, or variable-style flags inside attribute values.
- Do not collapse two different people into one entity only because they share the same label.
- If nothing clearly changed, output {{"entities": [], "relationships": []}}.

CURRENT WORLD MODEL:
{graph_json}

RECENT CHAT:
{history_text}
"""
