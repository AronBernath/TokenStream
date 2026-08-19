import { useMemo, useRef } from "react";
import type { ReactNode } from "react";

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (character) => {
    switch (character) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case '"':
        return "&quot;";
      default:
        return "&#039;";
    }
  });
}

function highlightJson(value: string) {
  const tokenPattern =
    /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|\b(true|false)\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[{}[\],:]/g;
  let output = "";
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = tokenPattern.exec(value)) !== null) {
    const token = match[0];
    output += escapeHtml(value.slice(lastIndex, match.index));

    if (match[1]) {
      const keyMatch = token.match(/^("(?:\\.|[^"\\])*")(\s*:)$/);
      if (keyMatch) {
        output += `<span class="json-key">${escapeHtml(keyMatch[1])}</span><span class="json-punctuation">${escapeHtml(keyMatch[2])}</span>`;
      } else {
        output += `<span class="json-key">${escapeHtml(token)}</span>`;
      }
    } else if (match[2]) {
      output += `<span class="json-string">${escapeHtml(token)}</span>`;
    } else if (token === "true" || token === "false") {
      output += `<span class="json-boolean">${token}</span>`;
    } else if (token === "null") {
      output += `<span class="json-null">${token}</span>`;
    } else if (/^-?\d/.test(token)) {
      output += `<span class="json-number">${token}</span>`;
    } else if (token === "{" || token === "}" || token === "[" || token === "]") {
      output += `<span class="json-brace">${escapeHtml(token)}</span>`;
    } else {
      output += `<span class="json-punctuation">${escapeHtml(token)}</span>`;
    }
    lastIndex = tokenPattern.lastIndex;
  }

  output += escapeHtml(value.slice(lastIndex));
  return output.endsWith("\n") ? `${output} ` : output;
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`card ${className}`}>{children}</section>;
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <p>{body}</p>
    </div>
  );
}

export function Field({
  label,
  children,
  hint
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}

export function JsonTextarea({
  value,
  onChange,
  minRows = 18,
  readOnly = false
}: {
  value: string;
  onChange: (value: string) => void;
  minRows?: number;
  readOnly?: boolean;
}) {
  const highlightRef = useRef<HTMLPreElement>(null);
  const highlighted = useMemo(() => highlightJson(value), [value]);
  const minHeight = `${minRows * 1.45}rem`;

  return (
    <div className={`json-editor-shell ${readOnly ? "is-readonly" : ""}`} style={{ minHeight }}>
      <pre ref={highlightRef} className="json-editor-highlight" aria-hidden="true">
        <code dangerouslySetInnerHTML={{ __html: highlighted }} />
      </pre>
      <textarea
        className="json-editor"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onScroll={(event) => {
          if (!highlightRef.current) return;
          highlightRef.current.scrollTop = event.currentTarget.scrollTop;
          highlightRef.current.scrollLeft = event.currentTarget.scrollLeft;
        }}
        spellCheck={false}
        readOnly={readOnly}
        style={{ minHeight }}
      />
    </div>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "good" | "warn" | "bad" }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function PageActions({ children }: { children: ReactNode }) {
  return <div className="page-actions">{children}</div>;
}
