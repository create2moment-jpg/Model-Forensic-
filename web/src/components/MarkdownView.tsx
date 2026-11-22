import type { ReactNode } from "react";

/** A small, safe Markdown renderer for documentation written by people and coding agents: headings (with anchors),
 *  paragraphs, lists, code fences, blockquotes, tables, inline code / bold / italic / links. Builds React elements
 *  only — never injects HTML — so untrusted documents cannot run script. */
export function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80);
}

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)|(\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    const k = `${key}-${i++}`;
    if (m[1]) out.push(<code key={k}>{t.slice(1, -1)}</code>);
    else if (m[2]) out.push(<b key={k}>{t.slice(2, -2)}</b>);
    else if (m[3]) out.push(<i key={k}>{t.slice(1, -1)}</i>);
    else {
      const lm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(t);
      const href = lm?.[2] ?? "";
      const label = lm?.[1] ?? t;
      const safe = /^(https?:|#|mailto:)/i.test(href);
      out.push(safe ? <a key={k} href={href} target={href.startsWith("#") ? undefined : "_blank"} rel="noreferrer">{label}</a> : <span key={k} className="mono">{label} ({href})</span>);
    }
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function MarkdownView({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const at = (j: number): string => lines[j] ?? "";
  const blocks: ReactNode[] = [];
  let i = 0;
  let n = 0;
  const key = () => `b${n++}`;
  while (i < lines.length) {
    const line = at(i);
    const fence = /^\s*(```|~~~)(.*)$/.exec(line);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !at(i).trim().startsWith(fence[1] ?? "```")) body.push(at(i++));
      i++;
      blocks.push(<pre key={key()} className="md-code"><code>{body.join("\n")}</code></pre>);
      continue;
    }
    const h = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (h) {
      const level = (h[1] ?? "#").length;
      const htext = h[2] ?? "";
      const Tag = `h${Math.min(level + 1, 6)}` as "h2";
      blocks.push(<Tag key={key()} id={slug(htext)}>{inline(htext, `h${n}`)}</Tag>);
      i++;
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(at(i + 1))) {
      const row = (l: string) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = row(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(at(i))) rows.push(row(at(i++)));
      blocks.push(
        <table key={key()} className="tbl md-table"><thead><tr>{head.map((c, j) => <th key={j}>{inline(c, `th${j}`)}</th>)}</tr></thead>
          <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, j) => <td key={j}>{inline(c, `td${ri}-${j}`)}</td>)}</tr>)}</tbody></table>,
      );
      continue;
    }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(at(i))) {
        items.push(at(i).replace(/^\s*([-*+]|\d+[.)])\s+/, ""));
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(at(i)) && !/^\s*([-*+]|\d+[.)])\s+/.test(at(i))) items[items.length - 1] += " " + at(i++).trim();
      }
      const L = ordered ? "ol" : "ul";
      blocks.push(<L key={key()}>{items.map((it, j) => <li key={j}>{inline(it, `li${n}-${j}`)}</li>)}</L>);
      continue;
    }
    if (/^\s*>/.test(line)) {
      const q: string[] = [];
      while (i < lines.length && /^\s*>/.test(at(i))) q.push(at(i++).replace(/^\s*>\s?/, ""));
      blocks.push(<blockquote key={key()}>{inline(q.join(" "), `q${n}`)}</blockquote>);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para: string[] = [];
    while (i < lines.length && at(i).trim() && !/^(#{1,6}\s|\s*(```|~~~)|\s*([-*+]|\d+[.)])\s+|\s*>|\s*\|)/.test(at(i))) para.push(at(i++));
    if (para.length === 0) { para.push(at(i++)); }
    blocks.push(<p key={key()}>{inline(para.join(" "), `p${n}`)}</p>);
  }
  return <div className="markdown">{blocks}</div>;
}
