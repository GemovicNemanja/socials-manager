/**
 * Block parsing for the journal's small markdown renderer (markdown.tsx), kept apart from the
 * components so it can be tested and reused without React.
 */

export type Block =
  | { type: "heading"; level: number; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "quote"; text: string }
  | { type: "code"; text: string };

const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const FENCE = /^\s*(```|~~~)/;
const QUOTE = /^\s*>\s?(.*)$/;

export function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (!line.trim()) {
      i++;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i]!.trim().startsWith(fence[1]!)) body.push(lines[i++]!);
      i++; // the closing fence (or the end)
      blocks.push({ type: "code", text: body.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1]!.length, text: heading[2]! });
      i++;
      continue;
    }
    const listKind = BULLET.test(line) ? BULLET : NUMBERED.test(line) ? NUMBERED : null;
    if (listKind) {
      const items: string[] = [];
      while (i < lines.length && lines[i]!.trim()) {
        const item = listKind.exec(lines[i]!);
        if (item) items.push(item[1]!);
        else if (items.length) items[items.length - 1] += ` ${lines[i]!.trim()}`; // a wrapped item
        i++;
      }
      blocks.push({ type: "list", ordered: listKind === NUMBERED, items });
      continue;
    }
    if (QUOTE.test(line)) {
      const body: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i]!)) body.push(QUOTE.exec(lines[i++]!)![1]!);
      blocks.push({ type: "quote", text: body.join(" ") });
      continue;
    }
    const body: string[] = [];
    while (
      i < lines.length &&
      lines[i]!.trim() &&
      !HEADING.test(lines[i]!) &&
      !FENCE.test(lines[i]!) &&
      !BULLET.test(lines[i]!) &&
      !NUMBERED.test(lines[i]!) &&
      !QUOTE.test(lines[i]!)
    ) {
      body.push(lines[i++]!.trim());
    }
    blocks.push({ type: "paragraph", text: body.join(" ") });
  }
  return blocks;
}

/** Only web, mail and in-page links; anything else (javascript:, data:) renders as plain text. */
export function safeHref(url: string): string | null {
  const href = url.trim();
  if (/^(https?:\/\/|mailto:)/i.test(href)) return href;
  if (/^(#|\.\/|\.\.\/|\/(?!\/))/.test(href)) return href;
  return null;
}
