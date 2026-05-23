# Piper Web UI — Frontend Shell

React + Vite + TypeScript frontend for the Piper Web UI bridge.

## Development

```bash
cd web_ui/frontend
npm install
npm run dev
```

The dev server starts on http://localhost:3000 (or next available port).

## Build

```bash
npm run build
```

Outputs to `web_ui/frontend/dist/`.
On the Piper startup path, `app.py` now auto-builds the frontend when `src/` is newer than `dist/`, so manual builds are only needed when you want to work on the frontend directly.

## Type-check only

```bash
npm run typecheck
```

## WebSocket URL

Default: `ws://127.0.0.1:8787/ws`

Override at build time:

```bash
VITE_PIPER_WS_URL=ws://localhost:8787/ws npm run dev
```

## Enabling Web UI mode in Piper

Set the environment variable before starting Piper:

```powershell
$env:PIPER_WEB_UI_ENABLED="true"
python app.py
```

Or permanently in your shell / `.env` equivalent.

When `WEB_UI_ENABLED=true`, Piper runs the BridgeServer on `127.0.0.1:8787/ws`
instead of launching DearPyGui.

## Frontend Layout

- `src/App.tsx` is the top-level orchestrator for bridge state and panel wiring
- `src/components/` contains the panel modules, including chat and fullscreen image views
- `src/css/` contains the modular stylesheet split imported through `src/css/index.css`
- The app is intentionally split into multiple modules now, rather than keeping everything in one file
