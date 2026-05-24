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
  imageUrl?: string;
}

export interface RawEvent {
  kind: string;
  sourceKind: string;
  payload: Record<string, unknown>;
  receivedAt: number;
}

export interface UiError {
  id: string;
  message: string;
  sourceKind: string;
  kind: string;
  receivedAt: number;
}

export type MicState = "idle" | "requesting_permission" | "listening" | "transcribing" | "error";

export type RailPanelId = "code" | "documents" | "system" | "activity" | "raw" | "capture";

export type TtsState = "idle" | "synthesizing" | "playing" | "error";

export interface MicStatus {
  state: "idle" | "listening" | "transcribing" | "error";
  stage?: string;
  message?: string;
  error?: string;
}
