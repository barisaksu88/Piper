Piper – Persona/Voice README (PV11)
Generated: 2025-08-21 00:47:02

Current knobs:
  - Sarcasm: FOLLOW_PERSONALITY
  - Max length: FOLLOW_PERSONALITY (120 chars)

Tone presets (prefix | suffix | end):
  - about       | '' | '' | '.'
  - confirm     | '✔ ' | '' | '.'
  - error       | '(!) ' | '' | '.'
  - error_hard  | '✖ ' | '' | '.'
  - greet       | '' | '' | '!'
  - info        | '' | '' | '.'
  - neutral     | '' | '' | '.'
  - status      | '✓ ' | '' | '.'
  - thinking    | '… ' | '' | '…'

Samples:
Greeting sample!
About sample.
Info sample.
✓ Status sample.
✔ Confirm sample.
… Thinking sample…
(!) Soft error sample.
✖ Hard error sample.

CLI commands (Services + Core glue):
  wake | sleep | about | time | date | version | help | exit
  persona preview
  persona sarcasm on|off|status
  persona max <n>|status|clear
  persona tone list | persona tone show <tone>
  persona tone set <tone> prefix|suffix|end "<text>"
  persona tone clear <tone>
  persona save <name> | persona load <name> | persona profiles | persona delete <name>

Notes:
  - personality.py remains user-owned/read-only; runtime overrides do not modify it.
  - Saved profiles live under C:\Piper\run\persona_<name>.json