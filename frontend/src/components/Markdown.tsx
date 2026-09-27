/**
 * Minimal Markdown for agent reports: headings, paragraphs, lists, tables, bold, italics,
 * inline code and links. Builds React elements (never raw HTML), so model output can't
 * inject markup; links are limited to http(s) and in-app paths.
 */
import type { ReactNode } from 'react'
import { parseBlocks } from '../lib/markdown'

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g

function safeHref(href: string): string | null {
  return /^(https?:\/\/|\/)/.test(href) ? href : null
}

function inline(text: string, keyBase = 'i'): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    const key = `${keyBase}-${i}`
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4)
      return <strong key={key}>{part.slice(2, -2)}</strong>
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2)
      return (
        <code key={key} className="rounded bg-paper px-1 text-[0.9em]">
          {part.slice(1, -1)}
        </code>
      )
    const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part)
    if (link) {
      const href = safeHref(link[2])
      return href ? (
        <a
          key={key}
          href={href}
          className="underline"
          target={href.startsWith('/') ? undefined : '_blank'}
          rel="noreferrer"
        >
          {link[1]}
        </a>
      ) : (
        link[1]
      )
    }
    if (/^(\*[^*\s][^*]*\*|_[^_\s][^_]*_)$/.test(part))
      return <em key={key}>{part.slice(1, -1)}</em>
    return part
  })
}

const HEADING = [
  '',
  'text-base font-semibold',
  'text-sm font-semibold mt-2',
  'text-sm font-medium',
  'text-sm',
]

export function Markdown({ text }: { text: string }) {
  return (
    <div className="flex flex-col gap-2 text-sm leading-relaxed">
      {parseBlocks(text).map((b, n) => {
        const k = `b${n}`
        switch (b.kind) {
          case 'heading': {
            const Tag = `h${Math.min(b.level + 1, 5)}` as 'h2'
            return (
              <Tag key={k} className={HEADING[b.level]}>
                {inline(b.text, k)}
              </Tag>
            )
          }
          case 'list': {
            const Tag = b.ordered ? 'ol' : 'ul'
            return (
              <Tag
                key={k}
                className={`${b.ordered ? 'list-decimal' : 'list-disc'} flex flex-col gap-0.5 pl-5`}
              >
                {b.items.map((it, j) => (
                  <li key={j}>{inline(it, `${k}-${j}`)}</li>
                ))}
              </Tag>
            )
          }
          case 'table':
            return (
              <div key={k} className="overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr>
                      {b.header.map((c, j) => (
                        <th
                          key={j}
                          className="border-b border-line px-2 py-1 text-left font-medium"
                        >
                          {inline(c, `${k}-h${j}`)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, j) => (
                      <tr key={j} className="border-b border-line/60">
                        {r.map((c, m) => (
                          <td key={m} className="px-2 py-1 tabular-nums">
                            {inline(c, `${k}-${j}-${m}`)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          default:
            return <p key={k}>{inline(b.text, k)}</p>
        }
      })}
    </div>
  )
}
