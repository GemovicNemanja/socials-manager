import type { ReactNode } from "react";
import { cx } from "../lib/cx";
import { parseBlocks, safeHref } from "./markdownParse";

/**
 * A small, safe markdown renderer for the owner's entries: paragraphs, headings, lists (bulleted and
 * numbered), block quotes, fenced code, and inline links, bold, italics and code. Everything else,
 * raw HTML included, is shown as text: the output is React elements only, never innerHTML.
 */

const INLINE: { kind: "code" | "link" | "strong" | "em"; re: RegExp }[] = [
  { kind: "code", re: /`([^`]+)`/ },
  { kind: "link", re: /\[([^\]]+)\]\(([^)\s]+)\)/ },
  { kind: "strong", re: /\*\*(.+?)\*\*|__(.+?)__/ },
  { kind: "em", re: /\*([^*\s][^*]*?)\*|(?<![\w])_([^_\s][^_]*?)_(?![\w])/ },
];

function renderInline(text: string, keyPrefix = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let rest = text;
  let n = 0;
  while (rest) {
    let best: { kind: (typeof INLINE)[number]["kind"]; match: RegExpExecArray } | null = null;
    for (const { kind, re } of INLINE) {
      const match = re.exec(rest);
      if (match && (best === null || match.index < best.match.index)) best = { kind, match };
    }
    if (!best) {
      out.push(rest);
      break;
    }
    const { kind, match } = best;
    if (match.index > 0) out.push(rest.slice(0, match.index));
    const key = `${keyPrefix}-${n++}`;
    if (kind === "code") {
      out.push(
        <code key={key} className="rounded-[3px] bg-surface px-1 py-px font-mono text-[0.86em] text-neutral">
          {match[1]}
        </code>,
      );
    } else if (kind === "link") {
      const href = safeHref(match[2]!);
      const label = renderInline(match[1]!, key);
      const external = href != null && /^https?:/i.test(href);
      out.push(
        href ? (
          <a
            key={key}
            href={href}
            className="text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
            {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
          >
            {label}
          </a>
        ) : (
          <span key={key}>{label}</span>
        ),
      );
    } else if (kind === "strong") {
      out.push(
        <strong key={key} className="font-medium text-text">
          {renderInline(match[1] ?? match[2]!, key)}
        </strong>,
      );
    } else {
      out.push(<em key={key}>{renderInline(match[1] ?? match[2]!, key)}</em>);
    }
    rest = rest.slice(match.index + match[0].length);
  }
  return out;
}

export interface MarkdownProps {
  source: string;
  /** "body" for entries and write-ups (15px), "small" for compact notes (13px). */
  size?: "body" | "small";
  className?: string;
}

export function Markdown({ source, size = "body", className }: MarkdownProps) {
  const text = size === "body" ? "text-[15px] leading-[1.65]" : "text-[13px] leading-[1.6]";
  return (
    <div className={cx("flex flex-col gap-4 font-sans text-muted", text, className)}>
      {parseBlocks(source).map((block, i) => {
        const key = `b${i}`;
        switch (block.type) {
          case "heading": {
            // The page title is the only h1; entry headings start at h2.
            const level = Math.min(6, Math.max(2, block.level));
            const Tag = `h${level}` as "h2" | "h3" | "h4" | "h5" | "h6";
            return (
              <Tag key={key} className={cx("pt-2 font-medium text-text", level === 2 ? "text-[17px]" : "text-[15px]")}>
                {renderInline(block.text, key)}
              </Tag>
            );
          }
          case "list": {
            const Tag = block.ordered ? "ol" : "ul";
            return (
              <Tag key={key} className={cx("flex flex-col gap-1.5 pl-5", block.ordered ? "list-decimal" : "list-disc")}>
                {block.items.map((item, j) => (
                  <li key={j} className="pl-1 marker:text-faint">
                    {renderInline(item, `${key}-${j}`)}
                  </li>
                ))}
              </Tag>
            );
          }
          case "quote":
            return (
              <blockquote key={key} className="border-l-2 border-rule-strong pl-4 text-muted italic">
                {renderInline(block.text, key)}
              </blockquote>
            );
          case "code":
            return (
              <pre key={key} className="overflow-x-auto rounded-sm border border-rule bg-raised px-3 py-2 font-mono text-xs leading-normal text-neutral">
                <code>{block.text}</code>
              </pre>
            );
          default:
            return <p key={key}>{renderInline(block.text, key)}</p>;
        }
      })}
    </div>
  );
}
