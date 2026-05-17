import type { ChatMessage } from "../types";

interface ChatPanelProps {
  messages: ChatMessage[];
  inputText: string;
  setInputText: (v: string) => void;
  onSend: () => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  chatBoxRef: React.RefObject<HTMLDivElement | null>;
  connState: string;
}

function isAssistant(role: string) {
  return role === "assistant";
}
function isUser(role: string) {
  return role === "user";
}

export default function ChatPanel({
  messages,
  inputText,
  setInputText,
  onSend,
  onKeyDown,
  chatBoxRef,
  connState,
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
            <div className="message-avatar">
              {isAssistant(m.role) ? (
                <div className="avatar-thumb assistant-thumb">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                    <path d="M2 17l10 5 10-5"/>
                    <path d="M2 12l10 5 10-5"/>
                  </svg>
                </div>
              ) : isUser(m.role) ? (
                <div className="avatar-thumb user-thumb">U</div>
              ) : (
                <div className="avatar-thumb system-thumb">S</div>
              )}
            </div>
            <div className="message-body">
              <div className="message-meta">
                <span className="message-author">{m.role}</span>
              </div>
              <pre className="message-content">{m.content}</pre>
            </div>
          </div>
        ))}
      </div>

      <div className="chat-input-area">
        <input
          className="chat-input"
          type="text"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Type a message..."
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
