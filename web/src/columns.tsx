import { createColumnHelper, type ColumnDef } from '@tanstack/react-table'
import type { Stock } from './types'
import { EMPTY, explainMissing, money, percent, plain, ratio } from './format'

const col = createColumnHelper<Stock>()

/**
 * Numeric accessors map null to `undefined` on purpose.
 *
 * TanStack applies `sortUndefined` before it inverts the comparator for a
 * descending sort, so undefined lands at the bottom in BOTH directions. Sorting
 * nulls as if they were zero would be actively misleading here: a company with
 * no 20-year history would rank alongside one that genuinely returned 0%.
 */
const num = (key: keyof Stock) => (row: Stock) => (row[key] ?? undefined) as number | undefined

type Fmt = (v: number | null) => string

function numericCell(key: keyof Stock, format: Fmt, opts: { signed?: boolean } = {}) {
  return function Cell({ row }: { row: { original: Stock } }) {
    const value = row.original[key] as number | null
    if (value == null) {
      return <span className="empty" title={explainMissing(row.original, key)}>{EMPTY}</span>
    }
    const cls = opts.signed ? (value >= 0 ? 'pos' : 'neg') : undefined
    return <span className={cls}>{format(value)}</span>
  }
}

function numeric(
  key: keyof Stock,
  header: string,
  format: Fmt,
  opts: { signed?: boolean; group: string; help?: string } ,
) {
  return col.accessor(num(key), {
    id: key,
    header,
    sortUndefined: 'last',
    sortDescFirst: true,
    meta: { align: 'right', group: opts.group, help: opts.help },
    cell: numericCell(key, format, opts),
  })
}

export const columns = [
  col.accessor('rank', {
    header: '#',
    meta: { align: 'right', group: 'Identity' },
    cell: (c) => <span className="rank">{c.getValue()}</span>,
    size: 56,
  }),
  col.accessor('symbol', {
    header: 'Ticker',
    meta: { group: 'Identity' },
    cell: (c) => <span className="ticker">{c.getValue()}</span>,
    size: 100,
  }),
  col.accessor('name', {
    header: 'Company',
    meta: { group: 'Identity' },
    cell: (c) => <span className="company">{c.getValue()}</span>,
    size: 240,
  }),
  col.accessor('country', {
    header: 'Country',
    meta: { group: 'Identity' },
    cell: (c) => plain(c.getValue()),
    size: 130,
  }),
  col.accessor('sector', {
    header: 'Sector',
    meta: { group: 'Identity' },
    cell: (c) => plain(c.getValue()),
    size: 160,
  }),
  col.accessor('exchange', {
    header: 'Exchange',
    meta: { group: 'Identity' },
    cell: (c) => plain(c.getValue()),
    size: 150,
  }),

  numeric('market_cap_usd', 'Market cap', money, { group: 'Size' }),
  numeric('price', 'Price', (v) => (v == null ? EMPTY : v.toFixed(2)), { group: 'Size' }),
  numeric('index_weight', 'Index wt.', (v) => percent(v == null ? null : v / 100, 3), {
    group: 'Size',
    help: 'Weight in the SPDR MSCI global index used to build this universe.',
  }),

  numeric('trailing_pe', 'P/E', ratio, { group: 'Valuation' }),
  numeric('forward_pe', 'Fwd P/E', ratio, { group: 'Valuation' }),
  numeric('price_to_book', 'P/B', ratio, { group: 'Valuation' }),
  numeric('price_to_sales', 'P/S', ratio, { group: 'Valuation' }),
  numeric('ev_to_ebitda', 'EV/EBITDA', ratio, { group: 'Valuation' }),
  numeric('dividend_yield', 'Div yield', (v) => percent(v, 2), { group: 'Valuation' }),

  numeric('return_1y', '1Y', (v) => percent(v), { signed: true, group: 'Growth',
    help: 'Annualised total return over 1 year.' }),
  numeric('return_3y', '3Y p.a.', (v) => percent(v), { signed: true, group: 'Growth',
    help: 'Annualised total return over 3 years.' }),
  numeric('return_5y', '5Y p.a.', (v) => percent(v), { signed: true, group: 'Growth',
    help: 'Annualised total return over 5 years.' }),
  numeric('return_10y', '10Y p.a.', (v) => percent(v), { signed: true, group: 'Growth',
    help: 'Annualised total return over 10 years.' }),
  numeric('return_20y', '20Y p.a.', (v) => percent(v), { signed: true, group: 'Growth',
    help: 'Annualised total return over 20 years.' }),

  numeric('revenue_cagr_3y', 'Rev 3Y', (v) => percent(v), { signed: true, group: 'Growth',
    help: 'Annualised revenue growth over 3 years.' }),
  numeric('revenue_cagr_5y', 'Rev 5Y', (v) => percent(v), { signed: true, group: 'Growth',
    help: 'Annualised revenue growth over 5 years.' }),
  numeric('revenue_cagr_10y', 'Rev 10Y', (v) => percent(v), { signed: true, group: 'Growth',
    help: 'Annualised revenue growth over 10 years. SEC/IFRS filers only.' }),
  numeric('net_income_cagr_5y', 'Profit 5Y', (v) => percent(v), { signed: true, group: 'Growth',
    help: 'Annualised net income growth over 5 years. Net income, not EPS, so share splits do not distort it.' }),
  numeric('net_income_cagr_10y', 'Profit 10Y', (v) => percent(v), { signed: true, group: 'Growth',
    help: 'Annualised net income growth over 10 years.' }),
  numeric('revenue_growth_ttm', 'Rev TTM', (v) => percent(v), { signed: true, group: 'Growth',
    help: 'Most recent trailing-twelve-month revenue growth.' }),

  numeric('profit_margin', 'Margin', (v) => percent(v), { signed: true, group: 'Quality' }),
  numeric('return_on_equity', 'ROE', (v) => percent(v), { signed: true, group: 'Quality' }),
  numeric('debt_to_equity', 'D/E', ratio, { group: 'Quality' }),

  numeric('beta', 'Beta', (v) => ratio(v, 2), { group: 'Risk' }),
  numeric('volatility_5y', 'Vol 5Y', (v) => percent(v), { group: 'Risk',
    help: 'Annualised volatility of monthly returns over 5 years.' }),
  numeric('max_drawdown', 'Max DD', (v) => percent(v), { signed: true, group: 'Risk',
    help: 'Largest peak-to-trough decline across the full price history.' }),
  numeric('pct_from_52w_high', 'From high', (v) => percent(v), { signed: true, group: 'Risk',
    help: 'Distance below the 52-week high.' }),
] as ColumnDef<Stock, any>[]   // eslint-disable-line @typescript-eslint/no-explicit-any

export const PRESETS: Record<string, string[]> = {
  Overview: ['rank', 'symbol', 'name', 'country', 'sector', 'market_cap_usd',
    'trailing_pe', 'return_1y', 'return_10y', 'return_20y', 'revenue_cagr_10y'],
  Valuation: ['rank', 'symbol', 'name', 'market_cap_usd', 'price', 'trailing_pe',
    'forward_pe', 'price_to_book', 'price_to_sales', 'ev_to_ebitda', 'dividend_yield'],
  Growth: ['rank', 'symbol', 'name', 'market_cap_usd', 'return_1y', 'return_3y',
    'return_5y', 'return_10y', 'return_20y', 'revenue_cagr_3y', 'revenue_cagr_5y',
    'revenue_cagr_10y', 'net_income_cagr_5y', 'net_income_cagr_10y'],
  Quality: ['rank', 'symbol', 'name', 'market_cap_usd', 'profit_margin',
    'return_on_equity', 'debt_to_equity', 'revenue_growth_ttm', 'trailing_pe'],
  Risk: ['rank', 'symbol', 'name', 'market_cap_usd', 'beta', 'volatility_5y',
    'max_drawdown', 'pct_from_52w_high', 'return_10y'],
}

export const ALL_COLUMN_IDS = columns.map((c) => (c as { id: string }).id)
