import type { ChatMessage } from "../types";

interface ChatPanelProps {
  messages: ChatMessage[];
  inputText: string;
  setInputText: (v: string) => void;
  onSend: () => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  chatBoxRef: React.RefObject<HTMLDivElement | null>;
  connState: string;
  userName?: string;
  authWaiting?: boolean;
}

function isAssistant(role: string) {
  return role === "assistant";
}
function isUser(role: string) {
  return role === "user";
}

function displayRole(role: string, userName: string): string {
  if (isAssistant(role)) return "Piper";
  if (isUser(role)) return userName || "User";
  return role;
}

export default function ChatPanel({
  messages,
  inputText,
  setInputText,
  onSend,
  onKeyDown,
  chatBoxRef,
  connState,
  userName = "User",
  authWaiting = false,
}: ChatPanelProps) {
  return (
    <div className="chat-panel">
      <div className="chat-panel-header">
        <h2>Conversation</h2>
      </div>

      <div className="chat-messages" ref={chatBoxRef}>
        {messages.map((m) => (
          <div
            key={m.id}
            className={`message-bubble ${m.role} ${
              m.streaming ? "streaming" : ""
            }`}
          >
            {!isUser(m.role) && (
              <div className="message-avatar">
                {isAssistant(m.role) ? (
                  <div className="avatar-thumb assistant-thumb">
                    <img
                      src="/piper-avatar.png"
                      alt="Piper"
                      style={{ width: "100%", height: "100%", borderRadius: "50%", objectFit: "cover" }}
                    />
                  </div>
                ) : (
                  <div className="avatar-thumb system-thumb">S</div>
                )}
              </div>
            )}
            <div className="message-body">
              <div className="message-meta">
                <span className="message-author">{displayRole(m.role, userName)}</span>
              </div>
              <pre className="message-content">{m.content}</pre>
              {m.imageUrl && (
                <div className="message-image">
                  <img
                    src={m.imageUrl}
                    alt={m.content || "Image"}
                    className="chat-image-thumb"
                    onClick={() => window.open(m.imageUrl, "_blank")}
                    onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                  />
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="chat-input-area">
        <input
          className="chat-input"
          type={authWaiting ? "password" : "text"}
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={authWaiting ? "Enter password or /cancel..." : "Type a message..."}
          disabled={connState !== "connected"}
        />
        <button
          className="send-btn"
          onClick={onSend}
          disabled={connState !== "connected" || !inputText.trim()}
        >
          ➤
        </button>
      </div>
    </div>
  );
}
