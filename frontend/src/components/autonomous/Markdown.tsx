import { useMemo } from "react";

/**
 * A tiny, dependency-free, safe Markdown renderer for LLM-authored prose
 * (gap theses, node rationales). The synthesis model emits real Markdown —
 * `**bold**`, `##` headings, `>` quotes, `-`/`1.` lists, `` `code` ``, and even
 * `|` tables — which previously rendered as an unformatted wall of literal
 * syntax that swallowed the whole page.
 *
 * Safety: input is HTML-escaped FIRST, then a whitelist of inline/block
 * transforms is applied to the escaped text, so no untrusted markup can reach
 * the DOM. We only ever inject spans/em/strong/code/headers/lists/blockquotes.
 */

function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// Inline: code, bold, italic, links → escaped-safe HTML. Operates on already-
// escaped text, so the only tags introduced are our own.
function inline(escaped: string): string {
  return escaped
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    // [text](http…) — href is validated to http(s) only.
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>');
}

interface Block {
  html: string;
}

function toBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;

  const flushPara = (buf: string[]) => {
    if (buf.length) blocks.push({ html: `<p>${inline(esc(buf.join(" ")))}</p>` });
  };

  let para: string[] = [];
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // blank line → paragraph break
    if (!trimmed) { flushPara(para); para = []; i++; continue; }

    // heading (#, ##, ### …) → h4/h5 (kept small; these are inside a panel)
    const h = /^(#{1,6})\s+(.*)$/.exec(trimmed);
    if (h) {
      flushPara(para); para = [];
      const level = Math.min(6, Math.max(4, h[1].length + 3)); // #→h4, ##→h5…
      blocks.push({ html: `<h${level} class="md-h">${inline(esc(h[2]))}</h${level}>` });
      i++; continue;
    }

    // table (| a | b |) → collapse a contiguous run into a real table
    if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
      flushPara(para); para = [];
      const rows: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) { rows.push(lines[i].trim()); i++; }
      const parsed = rows
        .map((r) => r.slice(1, -1).split("|").map((c) => c.trim()))
        .filter((cells) => !cells.every((c) => /^[-:]*$/.test(c))); // drop --- separator
      if (parsed.length) {
        const [head, ...body] = parsed;
        const thead = `<tr>${head.map((c) => `<th>${inline(esc(c))}</th>`).join("")}</tr>`;
        const tbody = body.map((r) => `<tr>${r.map((c) => `<td>${inline(esc(c))}</td>`).join("")}</tr>`).join("");
        blocks.push({ html: `<div class="md-table-wrap"><table class="md-table"><thead>${thead}</thead><tbody>${tbody}</tbody></table></div>` });
      }
      continue;
    }

    // blockquote
    if (trimmed.startsWith(">")) {
      flushPara(para); para = [];
      const quote: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        quote.push(lines[i].trim().replace(/^>\s?/, "")); i++;
      }
      blocks.push({ html: `<blockquote class="md-quote">${inline(esc(quote.join(" ")))}</blockquote>` });
      continue;
    }

    // list (bulleted or numbered) — collapse a contiguous run
    const isBullet = /^[-*]\s+/.test(trimmed);
    const isNum = /^\d+\.\s+/.test(trimmed);
    if (isBullet || isNum) {
      flushPara(para); para = [];
      const items: string[] = [];
      const re = isBullet ? /^[-*]\s+/ : /^\d+\.\s+/;
      while (i < lines.length) {
        const t = lines[i].trim();
        if (isBullet ? /^[-*]\s+/.test(t) : /^\d+\.\s+/.test(t)) {
          items.push(`<li>${inline(esc(t.replace(re, "")))}</li>`); i++;
        } else break;
      }
      const tag = isNum ? "ol" : "ul";
      blocks.push({ html: `<${tag} class="md-list">${items.join("")}</${tag}>` });
      continue;
    }

    // default: accumulate into a paragraph
    para.push(trimmed); i++;
  }
  flushPara(para);
  return blocks;
}

export default function Markdown({ text, className }: { text: string; className?: string }) {
  const html = useMemo(() => toBlocks(text || "").map((b) => b.html).join(""), [text]);
  return (
    <div
      className={`md${className ? " " + className : ""}`}
      // Safe: all content is HTML-escaped before our own whitelist of tags is applied.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
