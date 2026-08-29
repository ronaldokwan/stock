/**
 * The column taxonomy, defined once.
 *
 * This module is deliberately free of JSX and of runtime imports, so the test
 * suite can load it under `node --experimental-strip-types` and check the
 * derived exports without a bundler. `columns.tsx` turns these leaves into
 * TanStack column definitions; nothing else should hard-code a column list.
 */
import type { Stock } from './types'

/**
 * How a leaf renders, and therefore how it sorts: the numeric kinds go through
 * an accessor that maps null to undefined so gaps sort last in both directions.
 */
export type Kind =
  | 'text' | 'source'                                   // string cells
  | 'int' | 'money' | 'price' | 'percent' | 'ratio' | 'weight'  // numeric cells

const TEXT_KINDS: readonly Kind[] = ['text', 'source']

export function isTextKind(kind: Kind): boolean {
  return TEXT_KINDS.includes(kind)
}

/**
 * Default width per kind, in pixels.
 *
 * These are not cosmetic. The table lays out with `table-layout: fixed` so that
 * rendered widths match declared ones: the pinned columns are positioned from
 * the accumulated widths to their left, and any disagreement between the two
 * shunts a frozen column over the top of its neighbour.
 */
export const SIZE_BY_KIND: Record<Kind, number> = {
  text: 150,
  source: 90,
  int: 62,
  money: 112,
  price: 82,
  percent: 86,
  ratio: 84,
  weight: 92,
}

/** The benchmark statistic a column publishes in the summary row. */
export type Aggregate = 'median' | 'total' | 'none'

/**
 * Default aggregate per kind.
 *
 * `price` is the one that matters: the column holds 29 different listing
 * currencies, so an average of it is a meaningless number that would look
 * entirely plausible sitting under the table. `money` and `weight` take a sum
 * because the total market cap and the universe's share of the index are the
 * informative figures there, not a typical value. `int` covers rank, which is
 * ordinal.
 */
export const AGGREGATE_BY_KIND: Record<Kind, Aggregate> = {
  percent: 'median',
  ratio: 'median',
  money: 'total',
  weight: 'total',
  price: 'none',
  int: 'none',
  text: 'none',
  source: 'none',
}

export interface Leaf {
  id: keyof Stock
  /** Short label. The group banner above it supplies the context. */
  header: string
  /** `NO_GROUP` for the identity spine, which carries no banner. */
  group: string
  kind: Kind
  /** Render negatives red and non-negatives green. */
  signed?: boolean
  /** Decimal places for the percent, ratio and weight kinds. */
  digits?: number
  /** Header tooltip. Says what the number means, not how to sort. */
  help?: string
  /** Extra class on the value span, for the three identity columns. */
  className?: string
  /**
   * Boolean field marking this value as computed by the pipeline rather than
   * taken from the source. Rendered with a marker so the two are never
   * silently mixed in one column.
   */
  derivedFlag?: keyof Stock
  /** Overrides the kind's default benchmark statistic. */
  aggregate?: Aggregate
  size?: number
}

/** Identity columns: always visible, pinned to the left edge, never grouped. */
export const SPINE = ['rank', 'symbol', 'name'] as const

export const NO_GROUP = ''

/**
 * Every column, in display order.
 *
 * Order matters twice over: `buildColumns` banners each run of leaves sharing a
 * group, so a group's members must stay contiguous, and `resolvePreset` returns
 * ids in this order so an exported CSV matches the table it came from.
 *
 * `revenue_cagr_20y` is intentionally absent — no free source provides 20 years
 * of global fundamentals, so it is null for every row. See the README.
 */
export const LEAVES: Leaf[] = [
  { id: 'rank', header: '#', group: NO_GROUP, kind: 'int', className: 'rank', size: 56 },
  { id: 'symbol', header: 'Ticker', group: NO_GROUP, kind: 'text', className: 'ticker', size: 100 },
  { id: 'name', header: 'Company', group: NO_GROUP, kind: 'text', className: 'company', size: 240 },

  { id: 'country', header: 'Country', group: 'Classification', kind: 'text', size: 130 },
  { id: 'sector', header: 'Sector', group: 'Classification', kind: 'text', size: 160 },
  { id: 'industry', header: 'Industry', group: 'Classification', kind: 'text', size: 190 },
  { id: 'exchange', header: 'Exchange', group: 'Classification', kind: 'text', size: 150 },

  { id: 'market_cap_usd', header: 'Market cap', group: 'Size', kind: 'money' },
  { id: 'price', header: 'Price', group: 'Size', kind: 'price',
    help: 'Last close, in the listing currency rather than USD.' },
  { id: 'index_weight', header: 'Index wt.', group: 'Size', kind: 'weight', digits: 3,
    help: 'Weight in the SPDR MSCI global index used to build this universe.' },

  { id: 'trailing_pe', header: 'P/E', group: 'Valuation', kind: 'ratio',
    derivedFlag: 'trailing_pe_derived' },
  { id: 'forward_pe', header: 'Fwd P/E', group: 'Valuation', kind: 'ratio' },
  { id: 'price_to_book', header: 'P/B', group: 'Valuation', kind: 'ratio' },
  { id: 'price_to_sales', header: 'P/S', group: 'Valuation', kind: 'ratio' },
  { id: 'ev_to_ebitda', header: 'EV/EBITDA', group: 'Valuation', kind: 'ratio', size: 100 },
  { id: 'dividend_yield', header: 'Div yield', group: 'Valuation', kind: 'percent', digits: 2, size: 96 },

  { id: 'return_1y', header: '1Y', group: 'Total return p.a.', kind: 'percent', signed: true,
    help: 'Total shareholder return over 1 year.' },
  { id: 'return_3y', header: '3Y', group: 'Total return p.a.', kind: 'percent', signed: true,
    help: 'Annualised total shareholder return over 3 years.' },
  { id: 'return_5y', header: '5Y', group: 'Total return p.a.', kind: 'percent', signed: true,
    help: 'Annualised total shareholder return over 5 years.' },
  { id: 'return_10y', header: '10Y', group: 'Total return p.a.', kind: 'percent', signed: true,
    help: 'Annualised total shareholder return over 10 years.' },
  { id: 'return_20y', header: '20Y', group: 'Total return p.a.', kind: 'percent', signed: true,
    help: 'Annualised total shareholder return over 20 years. The only 20-year '
      + 'measure available: no free source provides 20 years of global fundamentals.' },

  { id: 'revenue_cagr_3y', header: '3Y', group: 'Revenue growth', kind: 'percent', signed: true,
    help: 'Annualised revenue growth over 3 years.' },
  { id: 'revenue_cagr_5y', header: '5Y', group: 'Revenue growth', kind: 'percent', signed: true,
    help: 'Annualised revenue growth over 5 years.' },
  { id: 'revenue_cagr_10y', header: '10Y', group: 'Revenue growth', kind: 'percent', signed: true,
    help: 'Annualised revenue growth over 10 years. SEC and IFRS filers only.' },
  { id: 'revenue_growth_ttm', header: 'TTM', group: 'Revenue growth', kind: 'percent', signed: true,
    help: 'Most recent trailing-twelve-month revenue growth. A single period, not annualised.' },

  { id: 'net_income_cagr_3y', header: '3Y', group: 'Profit growth', kind: 'percent', signed: true,
    help: 'Annualised net income growth over 3 years. Net income rather than EPS, '
      + 'so share splits do not distort it.' },
  { id: 'net_income_cagr_5y', header: '5Y', group: 'Profit growth', kind: 'percent', signed: true,
    help: 'Annualised net income growth over 5 years.' },
  { id: 'net_income_cagr_10y', header: '10Y', group: 'Profit growth', kind: 'percent', signed: true,
    help: 'Annualised net income growth over 10 years. SEC and IFRS filers only.' },
  { id: 'earnings_growth_ttm', header: 'TTM', group: 'Profit growth', kind: 'percent', signed: true,
    help: 'Most recent trailing-twelve-month earnings growth. A single period, not annualised.' },

  { id: 'profit_margin', header: 'Margin', group: 'Quality', kind: 'percent', signed: true },
  { id: 'return_on_equity', header: 'ROE', group: 'Quality', kind: 'percent', signed: true },
  { id: 'debt_to_equity', header: 'D/E', group: 'Quality', kind: 'ratio' },

  { id: 'beta', header: 'Beta', group: 'Risk', kind: 'ratio', digits: 2,
    help: 'Sensitivity to the market. Grouped with risk rather than quality '
      + 'because it measures volatility, not profitability.' },
  { id: 'volatility_5y', header: 'Vol 5Y', group: 'Risk', kind: 'percent',
    help: 'Annualised volatility of monthly returns over 5 years.' },
  { id: 'max_drawdown', header: 'Max DD', group: 'Risk', kind: 'percent', signed: true,
    help: 'Largest peak-to-trough decline across the full price history.' },
  { id: 'pct_from_52w_high', header: 'From high', group: 'Risk', kind: 'percent', signed: true, size: 96,
    help: 'Distance below the 52-week high.' },

  { id: 'fundamentals_source', header: 'Source', group: 'Provenance', kind: 'source', size: 90,
    help: 'Where this row’s revenue and profit history comes from.' },
  { id: 'fundamentals_years', header: 'Yrs', group: 'Provenance', kind: 'int', size: 60,
    help: 'Years of annual financials behind the growth columns.' },
  { id: 'history_start', header: 'History from', group: 'Provenance', kind: 'text', size: 120,
    help: 'First date of available price history. A short history is why a '
      + 'long-horizon return can be missing.' },
]

/** Every column id, in display order. */
export const ALL_COLUMN_IDS: string[] = LEAVES.map((leaf) => leaf.id)

/** Leaf lookup, for consumers that hold a column id rather than the leaf. */
export const LEAF_BY_ID: Record<string, Leaf> =
  Object.fromEntries(LEAVES.map((leaf) => [leaf.id, leaf]))

/** Group banners, in display order, excluding the unbannered spine. */
export const GROUPS: string[] =
  [...new Set(LEAVES.map((leaf) => leaf.group))].filter((g) => g !== NO_GROUP)

export interface PresetDef {
  /** Whole groups to show. */
  groups?: string[]
  /** Individual columns to show on top of those groups. */
  extra?: string[]
}

/**
 * Column presets. The spine is implicit in every one.
 *
 * All but Overview are expressed as group membership, so a column added to a
 * group joins its preset automatically and the two cannot drift apart. Overview
 * stays hand-picked: it is a curated cross-section rather than a group.
 */
export const PRESETS: Record<string, PresetDef> = {
  Overview: {
    extra: ['country', 'sector', 'market_cap_usd', 'trailing_pe',
      'return_1y', 'return_10y', 'return_20y', 'revenue_cagr_10y'],
  },
  Valuation: { groups: ['Size', 'Valuation'] },
  Returns: { groups: ['Total return p.a.'], extra: ['market_cap_usd'] },
  Business: {
    groups: ['Revenue growth', 'Profit growth'],
    extra: ['market_cap_usd', 'fundamentals_source'],
  },
  Quality: {
    groups: ['Quality'],
    extra: ['market_cap_usd', 'trailing_pe', 'revenue_growth_ttm'],
  },
  Risk: { groups: ['Risk'], extra: ['market_cap_usd', 'return_10y'] },
}

/** The column ids a preset shows, in display order. */
export function resolvePreset(name: string): string[] {
  const def = PRESETS[name] ?? PRESETS.Overview
  const wanted = new Set<string>(SPINE)
  for (const group of def.groups ?? []) {
    for (const leaf of LEAVES) {
      if (leaf.group === group) wanted.add(leaf.id)
    }
  }
  for (const id of def.extra ?? []) wanted.add(id)
  return ALL_COLUMN_IDS.filter((id) => wanted.has(id))
}
