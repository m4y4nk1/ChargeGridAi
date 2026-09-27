/** Block-level Markdown parsing for agent reports (rendered by components/Markdown). */

export type Block =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'para'; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] }
  | { kind: 'table'; header: string[]; rows: string[][] }

const cells = (line: string) =>
  line
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((c) => c.trim())

export function parseBlocks(md: string): Block[] {
  const lines = md.replace(/\r/g, '').split('\n')
  const blocks: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const t = line.trim()
    if (!t) {
      i++
      continue
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(t)
    if (h) {
      blocks.push({ kind: 'heading', level: h[1].length, text: h[2] })
      i++
      continue
    }
    if (t.startsWith('|') && i + 1 < lines.length && /^\|?\s*:?-{2,}/.test(lines[i + 1].trim())) {
      const header = cells(t)
      const rows: string[][] = []
      i += 2
      while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(cells(lines[i++]))
      blocks.push({ kind: 'table', header, rows })
      continue
    }
    const bullet = /^([-*]|\d+[.)])\s+/
    if (bullet.test(t)) {
      const ordered = /^\d/.test(t)
      const items: string[] = []
      while (i < lines.length && bullet.test(lines[i].trim()))
        items.push(lines[i++].trim().replace(bullet, ''))
      blocks.push({ kind: 'list', ordered, items })
      continue
    }
    const para: string[] = []
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(#{1,4}\s|\||[-*]\s|\d+[.)]\s)/.test(lines[i].trim())
    )
      para.push(lines[i++].trim())
    if (para.length === 0) para.push(lines[i++].trim())
    blocks.push({ kind: 'para', text: para.join(' ') })
  }
  return blocks
}
