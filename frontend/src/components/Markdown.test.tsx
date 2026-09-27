import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { parseBlocks } from '../lib/markdown'
import { Markdown } from './Markdown'

describe('Markdown', () => {
  it('parses headings, lists, tables and paragraphs', () => {
    const blocks = parseBlocks(
      '## Answer\n\nThe plan selects **20** sites.\nIt costs ₹28.78 crore.\n\n- one\n- two\n\n1. first\n\n| Site | kW |\n|---|---|\n| A | 120 |',
    )
    expect(blocks.map((b) => b.kind)).toEqual(['heading', 'para', 'list', 'list', 'table'])
    expect(blocks[1]).toEqual({
      kind: 'para',
      text: 'The plan selects **20** sites. It costs ₹28.78 crore.',
    })
  })

  it('renders elements, never raw HTML, and drops unsafe links', () => {
    const { container } = render(
      <Markdown
        text={
          'Hello <img src=x onerror=alert(1)> [ok](https://example.org) [bad](javascript:alert(1)) **bold**'
        }
      />,
    )
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText(/<img src=x/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'ok' })).toHaveAttribute('href', 'https://example.org')
    expect(screen.queryByRole('link', { name: 'bad' })).toBeNull()
    expect(screen.getByText('bold').tagName).toBe('STRONG')
  })

  it('renders tables with headers', () => {
    render(<Markdown text={'| Site | Charge points |\n|---|---|\n| Hindustan Petroleum | 4 |'} />)
    expect(screen.getByRole('columnheader', { name: 'Charge points' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Hindustan Petroleum' })).toBeInTheDocument()
  })
})
