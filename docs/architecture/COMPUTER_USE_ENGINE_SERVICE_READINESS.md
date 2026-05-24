# ComputerUseEngine Service Readiness Audit

**Status:** Active audit  
**Source:** `core/engines/computer_use_engine.py`  
**Date:** 2026-05-23

---

## 1. Behavior Classification

**Bucket:** Lifecycle Engine (stateful orchestration with external resources)  
**NOT a pure direct-call utility.**

`ComputerUseEngine` is a stateful class that manages a live browser session (Playwright or HTTP fallback). It maintains mutable session state across multiple `BROWSER_OP` executions, uses threading locks for concurrent access safety, and exposes explicit lifecycle methods (`shutdown`, `suspend`).

Key engine characteristics:
- **Mutable state:** `_BrowserSessionState` (URL, HTML, nodes, history, field values)
- **External resource ownership:** Playwright browser instance, context, page
- **Threading:** `_playwright_lock` (RLock), `_playwright_owner_thread`
- **Lifecycle methods:** `shutdown()`, `suspend()`
- **Lazy initialization:** Instantiated on-demand by `core/agent.py` and torn down on agent shutdown/suspend
- **Side effects:** Real browser automation, HTTP requests, filesystem downloads

---

## 2. Caller Map

| Caller | Import / Usage |
|--------|----------------|
| `core/agent.py` | Lazy-init property `computer_use_engine`; calls `shutdown()`, `suspend()`; forwards `exec_browser_op()` |
| `core/executor.py` | Routes `COMPUTER_USE` stages to `agent.computer_use_engine.exec_browser_op()` |
| `core/browser_route_utils.py` | Builds `COMPUTER_USE` stage cards |
| `core/services/route_clarity.py` | Detects `COMPUTER_USE` stages in routing |
| `core/services/summary.py` | Parses `COMPUTER_USE_VERIFIED_RESULT` entries |

**Production callers:** 5+ files (agent, executor, routing, summary, browser utils).  
**Test/script callers:** 6 smoke tests in `scripts/`.

---

## 3. Import / Export Map

**Exports (`core/engines/computer_use_engine.py`):**
- `ComputerUseEngine` (class)
- `BrowserOpError`, `BrowserScopeError` (exceptions)

**Package exports:** `core/engines/__init__.py` does **not** export `ComputerUseEngine`.

---

## 4. Runtime Responsibilities

### 4.1 Browser Session Management
- Lazy-initializes Playwright browser or HTTP fallback backend
- Maintains `_BrowserSessionState` across multiple tool calls
- Tracks browser history (back/forward navigation)
- Enforces domain allowlists via `COMPUTER_USE_ALLOWED_HTTP_DOMAINS` config

### 4.2 Browser Operation Execution
- `exec_browser_op(payload, cancel_token)` — main entry point
- Actions: `navigate`, `click`, `type_text`, `extract_text`, `extract_topic`, `download`, `go_back`, `go_forward`, `screenshot`
- Returns structured `BROWSER_OP` result dicts

### 4.3 Scope Enforcement
- Blocks HTTP/HTTPS if `COMPUTER_USE_HTTP_ENABLED` is false
- Validates target host against allowed domains
- Blocks navigation outside configured scope

### 4.4 HTML Parsing & Extraction
- `_SimplePageParser` — custom HTMLParser for title, node tree, text extraction
- Topic-ranked extraction with heading-aware scoring
- Element inventory scanning for form fields, links, buttons

### 4.5 Download Management
- Saves downloaded files to workspace
- Labels downloads with selector, href, and path metadata

---

## 5. Safety Boundaries

- **Domain allowlist:** Live HTTP browser actions require `allowed_domains` in stage metadata and may be further restricted by `COMPUTER_USE_ALLOWED_HTTP_DOMAINS` config
- **Config kill switches:** `COMPUTER_USE_ENABLED`, `COMPUTER_USE_HTTP_ENABLED`
- **Scope errors:** `BrowserScopeError` raised for out-of-domain navigation
- **Cancellation:** `CancellationToken` checked throughout long-running operations

---

## 6. Dependencies

| Dependency | Nature |
|------------|--------|
| `playwright.sync_api` | Optional; real browser backend |
| `requests` | HTTP fallback backend |
| `core.services.verification` | `VerificationResult` type only |
| `core.services.computer_use_verifier` | Separate service; verifier was relocated, engine stays |
| `config.CFG` | Runtime config for enable flags and domain allowlist |

---

## 7. Mutable State, Lifecycle, Threading

| Aspect | Detail |
|--------|--------|
| Mutable state | `_session: _BrowserSessionState` (URL, HTML, nodes, history, fields) |
| Threading | `_playwright_lock: threading.RLock()` protects Playwright instance access |
| Owner thread | `_playwright_owner_thread` tracks which thread created Playwright |
| Lifecycle | `shutdown()` → resets session + closes browser; `suspend()` → closes browser only |
| Lazy init | `core/agent.py` creates on first `computer_use_engine` property access |
| Resource cleanup | `_reset_playwright_session()` closes page → context → browser → playwright |

---

## 8. Current Tests / Smokes

### 8.1 Pytest Unit Tests

**None.** No `test_computer_use_engine.py` exists in `tests/`. The engine requires a real Playwright installation and browser instance, making pure unit tests difficult without heavy mocking.

### 8.2 Smoke Tests

Six smoke tests in `scripts/` exercise `ComputerUseEngine` against real or fixture browser pages:

| Smoke test | Coverage |
|------------|----------|
| `computer_use_engine_smoke_test.py` | Basic fixture HTML page title extraction |
| `computer_use_harness_smoke_test.py` | End-to-end browser request routing |
| `computer_use_browser_followup_harness_smoke_test.py` | Browser follow-up behavior |
| `computer_use_back_followup_harness_smoke_test.py` | History navigation (go_back) |
| `computer_use_extract_download_harness_smoke_test.py` | Extraction + download in one turn |
| `computer_use_form_navigation_harness_smoke_test.py` | Form fill + navigation |
| `computer_use_navigation_download_harness_smoke_test.py` | Navigation + download |
| `computer_use_playwright_blocked_domain_harness_smoke_test.py` | Domain blocking |
| `computer_use_playwright_example_alt_prompt_harness_smoke_test.py` | Playwright with live site |
| `computer_use_playwright_localhost_engine_smoke_test.py` | Playwright localhost |
| `computer_use_playwright_example_engine_smoke_test.py` | Playwright example.com |

### 8.3 Indirect Coverage

`core/executor.py` integration tests exercise `COMPUTER_USE` stage execution paths, but do not directly test `ComputerUseEngine` internals.

---

## 9. Missing Coverage

| Area | Risk | Notes |
|------|------|-------|
| `_enforce_scope` | Medium | Domain blocking logic is safety-critical but untested at unit level |
| `_SimplePageParser` | Low | HTML parsing is tested indirectly by smoke tests |
| `exec_browser_op` action dispatch | Medium | Each action branch (navigate, click, type_text, etc.) lacks isolated tests |
| Playwright lifecycle | Medium | `shutdown`, `suspend`, `_reset_playwright_session` untested |
| Topic extraction scoring | Low | Complex scoring tested indirectly by smoke tests |
| Thread safety | High | `_playwright_lock` behavior untested under concurrent access |

**Critical observation:** The engine has **no pytest unit tests** because it requires a real browser backend. Adding meaningful deterministic unit tests would require either:
1. Heavy mocking of Playwright/browser APIs, or
2. Extracting pure helper functions (HTML parsing, scope enforcement, topic scoring) into a separate testable module.

---

## 10. Behavior That Must Not Change

If relocation were attempted (not recommended):
- Browser session lifecycle semantics
- Domain scope enforcement rules
- `exec_browser_op` action dispatch and result shapes
- Playwright thread ownership and locking
- `shutdown()` / `suspend()` cleanup order

However, **relocation is not recommended** for this module.

---

## 11. Recommended Target Path

**N/A.** `ComputerUseEngine` should remain in `core/engines/computer_use_engine.py`.

---

## 12. Recommendation

**C) Do not move; keep in `core/engines/`.**

**Rationale:**

`ComputerUseEngine` is **not** a pure direct-call utility. It is a **lifecycle engine** that:

1. **Owns mutable state** — `_BrowserSessionState` persists across multiple `exec_browser_op` calls
2. **Manages external resources** — Playwright browser instance, context, page; HTTP sessions
3. **Uses threading** — `_playwright_lock` (RLock) and `_playwright_owner_thread` for concurrent safety
4. **Has lifecycle methods** — `shutdown()` and `suspend()` are called by `core/agent.py` during agent teardown
5. **Produces side effects** — Real browser automation, filesystem downloads, HTTP requests
6. **Is lazily initialized** — `core/agent.py` creates it on first property access and tears it down on shutdown

These characteristics are the defining traits of an **engine** in Piper's architecture, not a service. The `AGENTS.md` classification distinguishes:
- **Services:** deterministic, stateless, no lifecycle hooks, no external resources
- **Engines:** may have state, lifecycle, hooks, registries, or external resource ownership

`ComputerUseEngine` fits squarely in the engine bucket. Moving it to `core/services/` would misrepresent its architectural role and set a dangerous precedent that stateful resource-owning components belong in the services layer.

**Contrast with relocated modules:**
- `computer_use_verifier.py` — pure functions, no state, no side effects → correctly moved to services
- `state_mutation.py` — pure functions, no state, no side effects → correctly moved to services
- `ComputerUseEngine` — stateful, resource-owning, lifecycle-managed → correctly stays in engines

**Recommended next steps:**

1. **Keep** `ComputerUseEngine` in `core/engines/computer_use_engine.py`.
2. **Do not** add it to `core/services/__init__.py` exports.
3. **Future improvement:** Extract pure helper functions (HTML parsing, topic scoring, scope enforcement rules) into a separate `core/services/computer_use_helpers.py` if unit test coverage is needed. The engine would remain in `core/engines/` and import the helpers from services.
4. **Doc update:** `ENGINE_UTILITY_CLASSIFICATION.md` should note that `computer_use_engine.py` is an engine, not a utility.

---

## 13. Doc References

Docs referencing `core/engines/computer_use_engine.py`:

- `docs/architecture/ENGINE_UTILITY_CLASSIFICATION.md` — incorrectly lists it as a Direct-Call Utility; should be corrected to note it is a lifecycle engine
- `docs/architecture/TRIGGER_FLOW.md` — line 1477 caller map
- `docs/specs/computer-use.md` — line 35 path reference
- `docs/WIP.md` — line 60 lists the file
- `docs/specs/engine-directory-audit.md` — correctly lists it under review
