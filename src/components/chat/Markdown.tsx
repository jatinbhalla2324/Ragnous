import { useEffect, useMemo, useRef, useState } from 'react';

/* ═══════════════════════════════════════════════════════════════
   Answer renderer

   The model writes light markdown. This turns it into real blocks —
   grouped lists, headings, code, images — rather than styling every
   line the same way. Type is near-black on white at a comfortable
   reading measure; emphasis carries meaning, colour does not.
   ═══════════════════════════════════════════════════════════════ */

type ListItem = { text: string; marker?: string };

type Block =
  | { kind: 'p'; text: string }
  | { kind: 'ul'; items: ListItem[] }
  | { kind: 'ol'; items: ListItem[] }
  | { kind: 'h'; text: string; level: number }
  | { kind: 'quote'; text: string }
  | { kind: 'img'; alt: string; src: string }
  | { kind: 'code'; text: string; lang?: string }
  | { kind: 'rule' };

const parseBlocks = (src: string): Block[] => {
  const blocks: Block[] = [];
  const lines = src.split('\n');

  let paragraph: string[] = [];
  let list: { ordered: boolean; items: ListItem[] } | null = null;
  let code: { lang?: string; lines: string[] } | null = null;

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push({ kind: 'p', text: paragraph.join(' ') });
      paragraph = [];
    }
  };

  const flushList = () => {
    if (list && list.items.length) {
      blocks.push(list.ordered ? { kind: 'ol', items: list.items } : { kind: 'ul', items: list.items });
    }
    list = null;
  };

  const flushAll = () => {
    flushParagraph();
    flushList();
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const t = line.trim();

    // Fenced code
    if (t.startsWith('```')) {
      if (code === null) {
        flushAll();
        code = { lang: t.slice(3).trim() || undefined, lines: [] };
      } else {
        blocks.push({ kind: 'code', text: code.lines.join('\n'), lang: code.lang });
        code = null;
      }
      continue;
    }
    if (code !== null) {
      code.lines.push(rawLine);
      continue;
    }

    if (!t) {
      flushAll();
      continue;
    }

    // A voice-agent signature line the backend appends — dropped, the UI
    // already labels who is speaking.
    if (/^[—-]\s*\*?Ragnous Voice Agent\*?$/i.test(t)) {
      flushAll();
      continue;
    }

    if (/^([-*_])\1{2,}$/.test(t)) {
      flushAll();
      blocks.push({ kind: 'rule' });
      continue;
    }

    const img = t.match(/^!\[(.*?)\]\((.*?)\)$/);
    if (img) {
      flushAll();
      blocks.push({ kind: 'img', alt: img[1], src: img[2] });
      continue;
    }

    const heading = t.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushAll();
      blocks.push({ kind: 'h', text: heading[2], level: heading[1].length });
      continue;
    }

    const quote = t.match(/^>\s?(.*)$/);
    if (quote) {
      flushAll();
      blocks.push({ kind: 'quote', text: quote[1] });
      continue;
    }

    const ordered = t.match(/^(\d+)[.)]\s+(.*)$/);
    if (ordered) {
      flushParagraph();
      if (!list || !list.ordered) {
        flushList();
        list = { ordered: true, items: [] };
      }
      list.items.push({ text: ordered[2], marker: ordered[1] });
      continue;
    }

    const bullet = t.match(/^[-*•]\s+(.*)$/);
    if (bullet) {
      flushParagraph();
      if (!list || list.ordered) {
        flushList();
        list = { ordered: false, items: [] };
      }
      list.items.push({ text: bullet[1] });
      continue;
    }

    // A line that is only **bold** reads as a small heading.
    const boldOnly = t.match(/^\*\*(.+?)\*\*[:：]?$/);
    if (boldOnly) {
      flushAll();
      blocks.push({ kind: 'h', text: boldOnly[1], level: 3 });
      continue;
    }

    flushList();
    paragraph.push(t);
  }

  if (code !== null && code.lines.length) {
    blocks.push({ kind: 'code', text: code.lines.join('\n'), lang: code.lang });
  }
  flushAll();
  return blocks;
};

/** Inline emphasis, code and links. */
const renderInline = (text: string): React.ReactNode[] => {
  const nodes: React.ReactNode[] = [];
  const regex =
    /(\*\*(.+?)\*\*|(?<!\*)\*([^*]+?)\*(?!\*)|`([^`]+?)`|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\))/g;
  let last = 0;
  let key = 0;
  let m: RegExpExecArray | null;

  while ((m = regex.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));

    if (m[2] !== undefined) {
      nodes.push(
        <strong key={key++} className="font-semibold text-ink">
          {m[2]}
        </strong>
      );
    } else if (m[3] !== undefined) {
      nodes.push(
        <em key={key++} className="italic">
          {m[3]}
        </em>
      );
    } else if (m[4] !== undefined) {
      nodes.push(
        <code
          key={key++}
          className="px-1.5 py-0.5 mx-px rounded-md bg-surface-2 border border-line font-mono text-[0.85em] text-ink"
        >
          {m[4]}
        </code>
      );
    } else if (m[5] !== undefined) {
      nodes.push(
        <a
          key={key++}
          href={m[6]}
          target="_blank"
          rel="noopener noreferrer"
          className="text-ink underline underline-offset-2 decoration-ink-4 hover:decoration-ink"
        >
          {m[5]}
        </a>
      );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes.length ? nodes : [text];
};

const Caret = () => <span className="stream-caret" aria-hidden="true" />;

/**
 * Reveals the answer at a readable pace on the turn it arrives, then settles.
 * Re-renders of an older message show it whole.
 */
export const Prose = ({ content, animate = false }: { content: string; animate?: boolean }) => {
  const [shown, setShown] = useState(animate ? '' : content);
  const cursor = useRef(animate ? 0 : content.length);
  const settled = useRef(!animate);

  useEffect(() => {
    if (!animate || settled.current) {
      setShown(content);
      return;
    }
    const tick = setInterval(() => {
      if (cursor.current < content.length) {
        cursor.current += 4;
        setShown(content.slice(0, cursor.current));
      } else {
        setShown(content);
        settled.current = true;
        clearInterval(tick);
      }
    }, 16);
    return () => clearInterval(tick);
  }, [animate, content]);

  const streaming = !settled.current && shown !== content;
  const blocks = useMemo(() => parseBlocks(shown), [shown]);

  return (
    <div className="text-[0.9375rem] leading-[1.75] text-ink-2 space-y-4">
      {blocks.map((b, i) => {
        const tail = streaming && i === blocks.length - 1;

        switch (b.kind) {
          case 'h':
            return (
              <h3
                key={i}
                className={`font-semibold text-ink pt-1 first:pt-0 ${
                  b.level <= 2 ? 'text-[1.0625rem]' : 'text-[0.9375rem]'
                }`}
              >
                {renderInline(b.text)}
                {tail && <Caret />}
              </h3>
            );

          case 'ul':
            return (
              <ul key={i} className="space-y-1.5 pl-1">
                {b.items.map((item, j) => (
                  <li key={j} className="relative pl-5">
                    <span className="absolute left-1 top-[0.72em] w-[5px] h-[5px] rounded-full bg-ink-4" />
                    {renderInline(item.text)}
                    {tail && j === b.items.length - 1 && <Caret />}
                  </li>
                ))}
              </ul>
            );

          case 'ol':
            return (
              <ol key={i} className="space-y-1.5 pl-1">
                {b.items.map((item, j) => (
                  <li key={j} className="relative pl-7">
                    <span className="absolute left-0 top-0 w-5 text-right text-[0.8125rem] font-medium text-ink-3 tabular-nums leading-[1.75]">
                      {item.marker}.
                    </span>
                    {renderInline(item.text)}
                    {tail && j === b.items.length - 1 && <Caret />}
                  </li>
                ))}
              </ol>
            );

          case 'quote':
            return (
              <blockquote
                key={i}
                className="border-l-2 border-line-strong pl-4 text-ink-3 italic"
              >
                {renderInline(b.text)}
                {tail && <Caret />}
              </blockquote>
            );

          case 'img':
            return (
              <img
                key={i}
                src={b.src}
                alt={b.alt}
                loading="lazy"
                className="rounded-xl w-full max-w-md border border-line"
              />
            );

          case 'code':
            return (
              <div key={i} className="rounded-xl border border-line overflow-hidden">
                {b.lang && (
                  <div className="px-3.5 py-1.5 bg-surface border-b border-line text-[0.6875rem] font-mono uppercase tracking-wider text-ink-3">
                    {b.lang}
                  </div>
                )}
                <pre className="p-4 bg-surface overflow-x-auto text-[0.8125rem] leading-relaxed font-mono text-ink">
                  <code>{b.text}</code>
                </pre>
              </div>
            );

          case 'rule':
            return <hr key={i} className="border-line" />;

          default:
            return (
              <p key={i}>
                {renderInline(b.text)}
                {tail && <Caret />}
              </p>
            );
        }
      })}
    </div>
  );
};
