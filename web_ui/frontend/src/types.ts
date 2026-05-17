export type FrameKind = "event" | "error" | "action";

export interface EventFrame {
  frame: "event";
  timestamp: string;
  requestId: string;
  kind: string;
  sourceKind: string;
  payload: Record<string, unknown>;
}

export interface ErrorFrame {
  frame: "error";
  timestamp: string;
  requestId: string;
  kind: string;
  message: string;
  payload: Record<string, unknown>;
}

export interface ActionFrame {
  frame: "action";
  timestamp: string;
  requestId: string;
  action: string;
  payload: Record<string, unknown>;
}

export type BackendFrame = EventFrame | ErrorFrame;

export type ConnectionState =
  | "disconnected"
  | "connecting"
  | "connected"
  | "error";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  streaming?: boolean;
  suppressed?: boolean;
}

export interface RawEvent {
  kind: string;
  sourceKind: string;
  payload: Record<string, unknown>;
  receivedAt: number;
}

export type MicState = "idle" | "requesting_permission" | "listening" | "transcribing" | "error";
