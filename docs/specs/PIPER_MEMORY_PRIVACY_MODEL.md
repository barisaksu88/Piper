# Piper Memory & Privacy Model

> How Piper handles memory, user identity, incognito mode, conversation persistence, and tool access control.
> This document serves as both the architectural reference and the implementation spec.

---

## Users

| User Type | ID Pattern | Admin? | Knowledge | Persistent Memory |
|-----------|-----------|--------|-----------|-------------------|
| **Baris (creator)** | `admin_baris` | Yes | Yes | Full — ChromaDB, world model, conversation history, logs |
| **Identified non-admin** | `friend_name`, `family_name`, etc. | No | Yes (session) | Session only (today). Per-user persistent memory planned (Phase 2). |
| **Unknown** | `unknown` | No | No | None. Session-only, erased on shutdown or identity change. |

---

## Incognito Mode (`knowledge = false`)

Only **Baris** can activate an incognito style. Non-admin users cannot access incognito styles.

| Mode | What Gets Recorded | What Gets Erased |
|------|-------------------|------------------|
| **Normal (knowledge = true)** | Everything — session memory, ChromaDB vectors, conversation history, world model updates, debug logs | Nothing |
| **Incognito (knowledge = false)** | **Nothing.** Session memory only. | Everything on: style change away from incognito, Piper shutdown, or session end. No vectors, no persistent history, no world model writes, no debug logs. |

**Trigger:** Baris selects a style with `knowledge = false` in its config.

**Purpose:** Discuss sensitive personal matters without leaving traces. When incognito ends, it is as if the conversation never happened.

---

## Unknown User Behavior

### While Unknown

- **Session memory:** Active (Piper can reference earlier in the same session)
- **ChromaDB / vectors:** NOT written
- **World model:** NOT updated
- **Persistent history:** NOT saved
- **Debug logs:** Written for debugging only

**Rationale:** Unknown users have no identity to attach memory to. Keeping their conversation in long-term memory serves no purpose and creates privacy risk.

### Unknown → Identified Transition

When the user reveals their identity (typed name) or Piper discerns it (voice recognition, pattern match):

**The conversation from the unknown phase persists as if it was always that user.**

Implementation detail (copy / re-attribute / summary) is left to the developer — whatever is most efficient:
- **Copy:** Unknown-phase conversation copied into identified user's history
- **Re-attribute:** Unknown-phase session records reassigned to the identified user
- **Summary:** A summary note appended to the identified user's first turn

The user experience must be: "Piper remembers everything we talked about, even before she knew my name."

### Unknown + Session Ends

If the user closes Piper while still unknown:

- Session memory is **erased**
- Debug logs are **kept** (for troubleshooting)
- Nothing persists to the next session

---

## Identified Non-Admin User

### Today

- Session memory works normally within a session
- Preferred style auto-loaded on identification
- ChromaDB vectors written against the active user context
- World model updates for that user's subgraph
- **No persistent per-user memory across sessions** — starts fresh each time

### Phase 2: Per-User Persistent Memory (Future)

Each identified user gets their own:
- ChromaDB collection (isolated vectors)
- Conversation history
- World model subgraph (already exists — linked to admin)
- Preferred style persistence

Activated automatically when user is identified. No manual action needed.

---

## Tool Access Control

### Access Matrix

| Tool / Domain | Admin (Baris) | Identified Non-Admin | Unknown | Notes |
|---------------|--------------|---------------------|---------|-------|
| **FILE_WORK** (file_ops.py) | ✅ | ❌ | ❌ | Reads/writes Baris's personal files |
| **WORKSPACE_*** (workspace_*.py) | ✅ | ❌ | ❌ | Bulk workspace organization |
| **RUN_CODE** (interpreter.py) | ✅ | ❌ | ❌ | Executes Python on Baris's machine |
| **LIVE_SCREEN** (live_screen.py) | ✅ | ❌ | ❌ | Views Baris's desktop |
| **SCREEN_CAPTURE** (screen_capture.py) | ✅ | ❌ | ❌ | Captures Baris's screen |
| **IMAGE_WORK** (image_gen.py) | ✅ | ✅ | ❌ | No privacy risk |
| **VISION** (vision.py) | ✅ | ✅ | ❌ | Analyzes user-provided images |
| **TTS** (tts.py) | ✅ | ✅ | ✅ | Voice output |
| **STT** (stt.py) | ✅ | ✅ | ✅ | Voice input |
| **SEARCH_WORK** (search.py) | ✅ | 🔶 | ❌ | Non-admin: scoped to non-sensitive collections only |
| **MEMORY_WORK** | ✅ | 🔶 | ❌ | Non-admin: own session memory only (today); own persistent collection (Phase 2) |
| **Computer use — web** | ✅ | ✅ | ❌ | Public web access |
| **Computer use — desktop** | ✅ | ❌ | ❌ | Controls Baris's mouse/keyboard |

**Legend:** ✅ Allowed | ❌ Blocked | 🔶 Scoped (limited access)

### Enforcement Behavior

When a non-admin user triggers a route that would use an admin-only tool:

1. **Before tool execution:** Check `active_profile.is_admin`. If false and tool is admin-only:
   - Block the tool call
   - Log the attempt (for security awareness)
   - Route to CHAT with a friendly explanation:
   
   > *"I can't access files or run code — only Baris can do that on this system. I can help you with chat, search the web, or answer questions though!"*

2. **Scoped tools (SEARCH_WORK, MEMORY_WORK):** Enforce collection scoping at the data layer. Non-admin queries are filtered to collections tagged `public` or their own user-scoped collection. Admin's personal document collections are excluded.

3. **Route-level guard:** The Router must never select FILE_WORK, RUN_CODE, or desktop computer-use domains for non-admin users. If the LLM suggests such a route, override to CHAT.

---

## Summary Matrix

| What | Admin (Normal) | Admin (Incognito) | Identified Non-Admin | Unknown |
|------|---------------|-------------------|---------------------|---------|
| Session memory | ✅ | ✅ (only this) | ✅ | ✅ |
| ChromaDB vectors | ✅ | ❌ | ✅ (session only) | ❌ |
| Conversation history | ✅ | ❌ | ✅ (session only) | ❌ |
| World model updates | ✅ | ❌ | ✅ | ❌ |
| Debug logs | ✅ | ❌ | ✅ | ✅ (debug only) |
| Persistent across sessions | ✅ | ❌ | ❌ (Phase 2) | ❌ |
| Auto-load preferred style | N/A (admin styles) | N/A | ✅ | N/A |
| FILE_WORK tool | ✅ | ❌ | ❌ | ❌ |
| RUN_CODE tool | ✅ | ❌ | ❌ | ❌ |
| LIVE_SCREEN tool | ✅ | ❌ | ❌ | ❌ |
| Computer use (web) | ✅ | ❌ | ✅ | ❌ |
| Computer use (desktop) | ✅ | ❌ | ❌ | ❌ |

---

## Open Questions

1. **Per-user memory Phase 2:** When? Blocked on validation of current identity system.
2. **Unknown→identified implementation:** Copy vs re-attribute vs summary — decide during coding based on data model constraints.
3. **Scoped SEARCH_WORK collections:** Which collections are tagged `public` vs `admin-private`? Needs metadata tagging in ChromaDB.
