import type { BackendFrame, ActionFrame, ConnectionState } from "./types";

export const WS_URL =
  import.meta.env.VITE_PIPER_WS_URL || "ws://127.0.0.1:8787/ws";

let requestIdCounter = 0;
function makeRequestId(): string {
  return `req-${++requestIdCounter}-${Date.now()}`;
}

function makeTimestamp(): string {
  return new Date().toISOString();
}

export interface BridgeCallbacks {
  onStateChange?: (state: ConnectionState) => void;
  onFrame?: (frame: BackendFrame) => void;
  onError?: (message: string) => void;
}

export class PiperBridge {
  private ws: WebSocket | null = null;
  private state: ConnectionState = "disconnected";
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly callbacks: BridgeCallbacks;
  private intentionalClose = false;

  constructor(callbacks: BridgeCallbacks = {}) {
    this.callbacks = callbacks;
  }

  connect(): void {
    if (this.ws) return;
    this.intentionalClose = false;
    this.setState("connecting");

    try {
      this.ws = new WebSocket(WS_URL);
    } catch (err) {
      this.setState("error");
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.setState("connected");
    };

    this.ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(String(ev.data));
        if (data.frame === "event" || data.frame === "error") {
          this.callbacks.onFrame?.(data as BackendFrame);
        }
      } catch {
        this.callbacks.onError?.("Failed to parse backend frame");
      }
    };

    this.ws.onclose = () => {
      this.ws = null;
      this.setState("disconnected");
      if (!this.intentionalClose) {
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = () => {
      this.setState("error");
    };
  }

  disconnect(): void {
    this.intentionalClose = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
    this.setState("disconnected");
  }

  sendAction(action: string, payload: Record<string, unknown> = {}): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this.callbacks.onError?.("WebSocket is not open");
      return;
    }
    const frame: ActionFrame = {
      frame: "action",
      timestamp: makeTimestamp(),
      requestId: makeRequestId(),
      action,
      payload,
    };
    this.ws.send(JSON.stringify(frame));
  }

  getState(): ConnectionState {
    return this.state;
  }

  private setState(next: ConnectionState): void {
    if (this.state === next) return;
    this.state = next;
    this.callbacks.onStateChange?.(next);
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 3000);
  }
}
