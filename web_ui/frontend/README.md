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
