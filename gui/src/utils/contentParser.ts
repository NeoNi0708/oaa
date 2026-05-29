/**
 * Content parser for rich chat bubbles.
 *
 * Splits message content into ordered ContentSegment[]:
 * - markdown  → rendered via marked.parse() (includes images rendered by marked)
 * - actions   → ActionButtons component
 * - chart     → ChartView component (preview only, no download)
 */

import { marked } from 'marked'

export interface ActionDef {
  label: string
  action: string
  action_id: string
  args?: Record<string, unknown>
}

export type ContentSegment =
  | { type: 'markdown'; html: string }
  | { type: 'actions'; actions: ActionDef[] }
  | { type: 'chart'; option: Record<string, unknown> }

/** Combined regex to find all oaa-actions/oaa-chart fenced blocks */
const SPECIAL_BLOCK_RE = /```(oaa-actions|oaa-chart)\n([\s\S]*?)```/g

interface _BlockMatch {
  start: number
  end: number
  segment: ContentSegment
}

function _parseActions(raw: string): ActionDef[] | null {
  try {
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return null
    return parsed.every((item: unknown) =>
      typeof item === 'object' && item !== null
      && typeof (item as Record<string, unknown>).label === 'string'
      && typeof (item as Record<string, unknown>).action === 'string'
    ) ? parsed as ActionDef[] : null
  } catch {
    return null
  }
}

function _parseChart(raw: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    return parsed as Record<string, unknown>
  } catch {
    return null
  }
}

/**
 * Parse message content into renderable segments.
 *
 * - Extracts ```oaa-actions and ```oaa-chart fenced blocks
 * - Everything else is rendered as markdown via marked.parse()
 * - Images within markdown are rendered by marked as normal <img> tags
 */
export function parseContent(text: string): ContentSegment[] {
  if (!text) return [{ type: 'markdown', html: '' }]

  const blocks: _BlockMatch[] = []
  let match: RegExpExecArray | null

  SPECIAL_BLOCK_RE.lastIndex = 0
  while ((match = SPECIAL_BLOCK_RE.exec(text)) !== null) {
    const [full, lang, jsonRaw] = match
    if (lang === 'oaa-actions') {
      const actions = _parseActions(jsonRaw)
      if (actions) {
        blocks.push({ start: match.index, end: match.index + full.length, segment: { type: 'actions', actions } })
        continue
      }
    }
    if (lang === 'oaa-chart') {
      const option = _parseChart(jsonRaw)
      if (option) {
        blocks.push({ start: match.index, end: match.index + full.length, segment: { type: 'chart', option } })
        continue
      }
    }
  }

  blocks.sort((a, b) => a.start - b.start)

  const segments: ContentSegment[] = []
  let cursor = 0

  for (const block of blocks) {
    if (block.start > cursor) {
      const mdText = text.slice(cursor, block.start)
      segments.push({
        type: 'markdown',
        html: marked.parse(mdText, { async: false, breaks: true }) as string,
      })
    }
    segments.push(block.segment)
    cursor = block.end
  }

  if (cursor < text.length) {
    segments.push({
      type: 'markdown',
      html: marked.parse(text.slice(cursor), { async: false, breaks: true }) as string,
    })
  }

  return segments.length > 0 ? segments : [{ type: 'markdown', html: '' }]
}
