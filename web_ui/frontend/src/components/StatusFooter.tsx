interface StatusFooterProps {
  statsText: string;
  modeText: string;
  styleText: string;
}

export default function StatusFooter({ statsText, modeText, styleText }: StatusFooterProps) {
  const time = new Date().toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  return (
    <footer className="status-footer">
      <div className="footer-left">
        <span className="footer-badge">MODEL Piper Local</span>
        <span className="footer-sep">·</span>
        <span className="footer-badge">CONTEXT 128k tokens</span>
        <span className="footer-sep">·</span>
        <span className="footer-badge privacy">PRIVACY 100% Local</span>
      </div>
      <div className="footer-right">
        {styleText && (
          <span className="footer-badge dim">STYLE {styleText}</span>
        )}
        {modeText && (
          <span className="footer-badge dim">{modeText}</span>
        )}
        {statsText && (
          <span className="footer-badge dim">{statsText}</span>
        )}
        <span className="footer-badge time">{time}</span>
      </div>
    </footer>
  );
}
