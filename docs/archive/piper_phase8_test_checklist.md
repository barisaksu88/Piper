# Piper Phase 8 — Hands-On Test Checklist

> Use this checklist to validate the LangGraph orchestrator during daily use.
> Print or keep this file open while testing. Check off each item as you go.

---

## Setup (Do This Once)

1. **Enable the graph orchestrator**
   - Open your Piper config or set environment variable:
   ```powershell
   $env:PIPER_USE_LANGGRAPH_ORCHESTRATOR="true"
   ```

2. **Enable debug tracing** (helps if you need to report an issue):
   ```powershell
   $env:PIPER_DEBUG_LANGGRAPH_TRACE="true"
   $env:PIPER_DEBUG_LANGGRAPH_VISUALIZE="true"
   ```

3. **Start Piper normally.** You should see in the UI log:
   ```
   [LANGGRAPH] Starting graph invocation.
   ```

---

## Test 1: Simple Chat (CHAT Route)

**What to type:**
```
What is the difference between a list and a tuple in Python?
```

**What should happen:**
- Piper answers immediately in a conversational way
- No "Working on it..." spinner from MANAGER
- UI log shows: `Final stage: PERSONA`

**Check:** ☐ Pass  ☐ Fail  ☐ Unsure

**If it fails:** Piper might route this to TASK and try to write a file. That's a regression.

---

## Test 2: Search Request (SEARCH Route)

**What to type:**
```
Search my notes for anything about deployment.
```

**What should happen:**
- Piper searches your knowledge base / workspace
- Returns results in a conversational summary
- No file creation or code editing

**Check:** ☐ Pass  ☐ Fail  ☐ Unsure

**If it fails:** Piper might ignore the search and just chat, or try to create a file with search results.

---

## Test 3: File Creation (TASK Route)

**What to type:**
```
Create a file called test_hello.txt in my workspace with the text "Hello World"
```

**What should happen:**
- Piper shows a "Working..." state (MANAGER running)
- File is created
- Piper confirms in a friendly message
- UI log shows stages: ROUTE → MANAGER → VERIFY → PERSONA

**Check:** ☐ Pass  ☐ Fail  ☐ Unsure

**Verify the file exists:** Open your workspace folder. `test_hello.txt` should be there.

---

## Test 4: File Edit (TASK Route)

**What to type:**
```
Add a line "Goodbye World" to the end of test_hello.txt
```

**What should happen:**
- Piper edits the file
- Confirms the change
- No unnecessary questions or confirmations (simple edit = no approval needed)

**Check:** ☐ Pass  ☐ Fail  ☐ Unsure

**Verify:** Open `test_hello.txt`. It should have both lines.

---

## Test 5: Approval-Required Operation (Interrupt)

**What to type:**
```
Delete the file test_hello.txt
```

**What should happen:**
- Piper **pauses** and asks for your confirmation
- You see a prompt like "Approve file deletion?" or similar in the UI
- The UI log shows: `[LANGGRAPH] Graph interrupted — awaiting user confirmation.`
- Piper **does not** delete the file until you respond

**Check:** ☐ Pass  ☐ Fail  ☐ Unsure

**Now click "Approve" (or type yes/confirm depending on your UI).**

**What should happen next:**
- File is deleted
- Piper confirms

**Check:** ☐ Pass  ☐ Fail  ☐ Unsure

**If it fails:** Piper deletes without asking, or freezes and never resumes after approval.

---

## Test 6: Deny an Operation (Interrupt + Rejection)

**What to type:**
```
Delete the file test_hello.txt
```

*(If the file was already deleted in Test 5, create it again first: `Create test_hello.txt with "test"`)*

**When Piper asks for approval, click "Deny" or "Cancel."**

**What should happen:**
- File is **not** deleted
- Piper acknowledges the cancellation
- Piper does **not** get stuck in a loop asking again

**Check:** ☐ Pass  ☐ Fail  ☐ Unsure

**If it fails:** Piper asks again immediately, or deletes anyway, or freezes.

---

## Test 7: Change Your Mind During Interrupt

**What to type:**
```
Create a file called old_name.txt with "content"
```

**When Piper asks for approval, instead of yes/no, say:**
```
Actually, name it new_name.txt instead
```

**What should happen:**
- Piper handles the change gracefully
- Creates `new_name.txt` (not `old_name.txt`)
- No crash

**Check:** ☐ Pass  ☐ Fail  ☐ Unsure

**If it fails:** Crash, or creates wrong file, or ignores your correction.

---

## Test 8: Multi-Turn Memory

**What to type (Turn 1):**
```
My favorite color is blue.
```

**Piper should:** Acknowledge normally.

**What to type (Turn 2 — new turn, not a reply):**
```
What is my favorite color?
```

**What should happen:**
- Piper remembers "blue"
- Answers correctly without you reminding it

**Check:** ☐ Pass  ☐ Fail  ☐ Unsure

**If it fails:** Piper says it doesn't know, or makes up a color.

---

## Test 9: Cancel Mid-Operation

**What to type:**
```
Write a Python script that counts to 1 million and print it to output.txt
```

**While Piper is working (spinner active), click Cancel / Stop.**

**What should happen:**
- Operation stops immediately
- No partial file corruption
- Piper acknowledges cancellation
- UI log shows: `Action canceled by user.`

**Check:** ☐ Pass  ☐ Fail  ☐ Unsure

**If it fails:** Piper continues running after cancel, or crashes.

---

## Test 10: Complex Task (Multiple Stages)

**What to type:**
```
Create a folder called demo_folder, create a file inside it called readme.md with "# Demo", and then list the contents of demo_folder
```

**What should happen:**
- Piper creates the folder
- Creates the file
- Lists contents
- All in one turn, or asks if it should proceed through multiple stages
- No crash, no infinite loop

**Check:** ☐ Pass  ☐ Fail  ☐ Unsure

**Verify:** `demo_folder/readme.md` exists with correct content.

---

## Test 11: Edge Case — Empty / Nonsense Input

**What to type:**
```
...
```

**Or:**
```
asdfghjkl
```

**What should happen:**
- Piper responds gracefully (maybe asks for clarification)
- No crash
- No file creation

**Check:** ☐ Pass  ☐ Fail  ☐ Unsure

---

## Test 12: Overnight / Long Session Stability

**Use Piper normally for 1–2 hours.** Ask a mix of:
- Chat questions
- File creations
- File edits
- Searches

**What to watch for:**
- Memory usage doesn't grow unbounded
- Response times don't get slower over time
- No mysterious errors in the UI log

**Check:** ☐ Stable after 1 hour  ☐ Slower / leak / crash

---

## Red Flags — Report Immediately

If any of these happen, stop testing and report:

| Symptom | Likely Problem |
|---------|---------------|
| Piper creates/deletes files without asking | Interrupt/approval bypass |
| Piper freezes and never responds | Graph deadlock or checkpoint failure |
| Same question asked in a loop | Routing stuck or interrupt not consumed |
| "LangGraph" error in UI log | Graph construction or invocation crash |
| Memory usage grows continuously | Checkpoint leak or state accumulation |
| Simple chat routed to TASK | ROUTE node regression |
| File edit creates a new file instead of editing | MANAGER/FILE_WORK regression |
| Approval asked for harmless read-only operation | Approval policy too aggressive |

---

## How to Report an Issue

Copy this template and fill it in:

```
**Test:** #(number)
**Prompt:** (what you typed)
**Expected:** (what should have happened)
**Actual:** (what actually happened)
**UI Log:** (paste relevant lines from the log, especially anything with [LANGGRAPH])
**Flag status:** PIPER_USE_LANGGRAPH_ORCHESTRATOR=true, PIPER_DEBUG_LANGGRAPH_TRACE=(true/false)
```

---

## Completion

**Date tested:** ___________
**Total passes:** ___ / 12
**Total fails:** ___ / 12
**Overall:** ☐ Ready to keep using  ☐ Needs fixes first

---

> Keep this file. Re-run Tests 1–4 after any future Piper update to catch regressions.
