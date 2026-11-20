import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Compact Markdown rendering for agent-produced text (chat messages,
 * task results). react-markdown + remark-gfm are MIT; the element
 * overrides below are this project's own compact styling so rendered
 * output matches the surrounding small-text UI instead of browser
 * defaults. Raw HTML stays disabled (react-markdown default), so
 * model output can never inject markup. */
export default function Markdown({ text, tone = "light" }: { text: string; tone?: "light" | "dark" }) {
  const dark = tone === "dark";
  return (
    <div className="md-body small" style={dark ? { color: "#fff" } : undefined}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p style={{ margin: "3px 0" }}>{children}</p>,
          ul: ({ children }) => <ul style={{ margin: "3px 0", paddingLeft: 18 }}>{children}</ul>,
          ol: ({ children }) => <ol style={{ margin: "3px 0", paddingLeft: 18 }}>{children}</ol>,
          li: ({ children }) => <li style={{ margin: "2px 0" }}>{children}</li>,
          h1: ({ children }) => <div style={{ fontWeight: 700, fontSize: 15, margin: "6px 0 3px" }}>{children}</div>,
          h2: ({ children }) => <div style={{ fontWeight: 700, fontSize: 14, margin: "6px 0 3px" }}>{children}</div>,
          h3: ({ children }) => <div style={{ fontWeight: 700, margin: "5px 0 2px" }}>{children}</div>,
          h4: ({ children }) => <div style={{ fontWeight: 700, margin: "5px 0 2px" }}>{children}</div>,
          code: ({ children }) => (
            <code style={{ background: dark ? "rgba(255,255,255,0.18)" : "#F1F1EF", borderRadius: 4, padding: "0 4px", fontSize: "0.92em" }}>{children}</code>
          ),
          pre: ({ children }) => (
            <pre style={{
              background: dark ? "rgba(255,255,255,0.10)" : "#F7F7F5", border: dark ? "1px solid rgba(255,255,255,0.25)" : "1px solid var(--border)", borderRadius: 8,
              padding: "8px 10px", overflowX: "auto", margin: "5px 0", fontSize: "0.92em",
            }}>{children}</pre>
          ),
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer" style={{ color: dark ? "#9CC3EE" : "#3978C6" }}>{children}</a>
          ),
          blockquote: ({ children }) => (
            <blockquote style={{ borderLeft: dark ? "2px solid rgba(255,255,255,0.4)" : "2px solid var(--border)", paddingLeft: 9, margin: "4px 0", color: dark ? "rgba(255,255,255,0.75)" : "var(--muted, #777)" }}>{children}</blockquote>
          ),
          table: ({ children }) => (
            <div style={{ overflowX: "auto", margin: "5px 0" }}>
              <table style={{ borderCollapse: "collapse" }}>{children}</table>
            </div>
          ),
          th: ({ children }) => <th style={{ border: "1px solid var(--border)", padding: "3px 8px", background: "#F7F7F5", textAlign: "left" }}>{children}</th>,
          td: ({ children }) => <td style={{ border: "1px solid var(--border)", padding: "3px 8px" }}>{children}</td>,
          hr: () => <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "7px 0" }} />,
        }}
      >
        {text || ""}
      </ReactMarkdown>
    </div>
  );
}
