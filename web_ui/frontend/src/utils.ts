import type { ChatMessage } from "./types";

export function sanitizeOperationalText(text: string): string {
  const clean = String(text || "").trim();
  if (!clean) return "";
  if (clean.toUpperCase().includes("SPEAK")) return "Generating";
  return clean;
}

export function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function isThinkingPlaceholder(m: ChatMessage): boolean {
  const text = m.content.trim();
  return (
    (m.role === "assistant" || m.role === "system") &&
    (text === "Thinking..." || text === "Thinking…" || text.startsWith("Thinking"))
  );
}

export function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const result = reader.result as string;
      const commaIdx = result.indexOf(",");
      resolve(commaIdx >= 0 ? result.slice(commaIdx + 1) : result);
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

export function chooseMimeType(): string {
  const prefs = ["audio/webm;codecs=opus", "audio/webm"];
  for (const t of prefs) {
    if (MediaRecorder.isTypeSupported(t)) return t;
  }
  return "";
}

export function formatFromMimeType(mime: string): "webm" | "wav" {
  if (mime.includes("wav")) return "wav";
  return "webm";
}
