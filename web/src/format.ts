import type { Stock } from './types'

/** Rendered in place of every missing value, so a gap never reads as a zero. */
export const EMPTY = '—'

export function money(value: number | null): string {
  if (value == null) return EMPTY
  const abs = Math.abs(value)
  if (abs >= 1e12) return `$${(value / 1e12).toFixed(2)}T`
  if (abs >= 1e9) return `$${(value / 1e9).toFixed(1)}B`
  if (abs >= 1e6) return `$${(value / 1e6).toFixed(0)}M`
  return `$${value.toFixed(0)}`
}

export function percent(value: number | null, digits = 1): string {
  if (value == null) return EMPTY
  return `${(value * 100).toFixed(digits)}%`
}

export function ratio(value: number | null, digits = 1): string {
  if (value == null) return EMPTY
  return value.toFixed(digits)
}

export function plain(value: string | null): string {
  return value == null || value === '' ? EMPTY : value
}

/**
 * Why a particular cell is empty. Shown as a tooltip so a gap is explained
 * rather than just blank — the honest-gaps rule the dataset is built on.
 */
export function explainMissing(row: Stock, columnId: string): string {
  if (columnId.startsWith('return_')) {
    const years = Number(columnId.match(/(\d+)y/)?.[1] ?? 0)
    if (row.history_start) {
      const start = new Date(row.history_start)
      const age = (Date.now() - start.getTime()) / (365.25 * 24 * 3600 * 1000)
      if (age < years) {
        return `Listed ${row.history_start} — only ${age.toFixed(1)} years of price history, so a ${years}-year return cannot be computed.`
      }
    }
    return `No ${years}-year price history available for ${row.symbol}.`
  }

  if (columnId.startsWith('revenue_cagr') || columnId.startsWith('net_income_cagr')) {
    const years = Number(columnId.match(/(\d+)y/)?.[1] ?? 0)
    if (columnId === 'revenue_cagr_20y') {
      return 'No free data source provides 20 years of global fundamentals. SEC XBRL structured data begins around 2009.'
    }
    if (row.fundamentals_source === 'none') {
      return `No annual financial statements found for ${row.symbol}.`
    }
    if (row.fundamentals_source === 'yahoo') {
      return `Only ${row.fundamentals_years} years of financials available (Yahoo). A ${years}-year growth rate needs ${years + 1}.`
    }
    return `SEC filings for ${row.symbol} cover ${row.fundamentals_years} years — not enough for a ${years}-year growth rate.`
  }

  // Not simply "may be lossmaking": Yahoo publishes no EPS at all for some
  // listings — every Korean line in the universe — and those are filled from
  // net income where possible. A dash that survives that means one of two
  // specific things, and profit margin tells them apart.
  if (columnId === 'trailing_pe') {
    if (row.profit_margin != null && row.profit_margin < 0) {
      return `${row.symbol} is lossmaking over the last twelve months, so a `
        + 'price/earnings ratio is undefined.'
    }
    return 'No P/E is published for this listing, and none could be derived: '
      + 'that needs a recent positive annual net income reported in the same '
      + 'currency as the market cap.'
  }
  if (columnId === 'forward_pe') {
    return 'No forward P/E — this one needs analyst earnings estimates, which '
      + 'are not published for every listing.'
  }
  if (columnId === 'price_to_book') {
    return 'No book value is published for this listing, so price/book cannot '
      + 'be computed.'
  }
  if (columnId === 'market_cap_usd') return 'Market cap unavailable, or no FX rate for this currency.'
  return 'Not available from the free data sources used.'
}

export function sourceLabel(source: Stock['fundamentals_source']): string {
  if (source === 'sec') return 'SEC EDGAR (XBRL)'
  if (source === 'yahoo') return 'Yahoo Finance'
  return 'none'
}

export function toCSV(rows: Stock[], columns: string[]): string {
  const escape = (v: unknown) => {
    if (v == null) return ''
    const s = String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const head = columns.join(',')
  const body = rows.map((r) =>
    columns.map((c) => escape((r as unknown as Record<string, unknown>)[c])).join(',')
  )
  return [head, ...body].join('\n')
}
