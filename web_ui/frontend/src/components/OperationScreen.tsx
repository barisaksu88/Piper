interface OperationScreenProps {
  steps: { name: string; status: "pending" | "done" | "error" }[];
  message: string;
  title?: string;
}

const ICONS = { pending: "○", done: "✓", error: "✗" };

export default function OperationScreen({ steps, message, title = "Booting" }: OperationScreenProps) {
  return (
    <div className="operation-screen">
      <div className="operation-content">
        <div className="operation-avatar">
          <img src="/piper-avatar.png" alt="Piper" />
        </div>
        <h2>{title}</h2>
        {message && <p className="operation-message">{message}</p>}
        {steps.length > 0 && (
          <div className="operation-steps">
            {steps.map((s, i) => (
              <div key={i} className={`operation-step ${s.status}`}>
                <span>{ICONS[s.status]}</span>
                <span>{s.name}</span>
              </div>
            ))}
          </div>
        )}
        <div className="operation-input-disabled">Input disabled while Piper is {title.toLowerCase()}...</div>
      </div>
    </div>
  );
}
