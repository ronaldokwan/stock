import { createColumnHelper, type ColumnDef } from '@tanstack/react-table'
import type { Stock } from './types'
import { EMPTY, explainMissing, money, percent, plain, ratio, sourceLabel } from './format'
import { isTextKind, LEAVES, NO_GROUP, SIZE_BY_KIND, type Leaf } from './columns.def'

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

const str = (key: keyof Stock) => (row: Stock) => (row[key] ?? undefined) as string | undefined

/**
 * Render one numeric value the way its column renders it. Shared with the
 * summary row, so a median formats exactly like the cells it summarises.
 */
export function formatNumber(leaf: Leaf, value: number): string {
  switch (leaf.kind) {
    case 'money': return money(value)
    case 'price': return value.toFixed(2)
    case 'percent': return percent(value, leaf.digits ?? 1)
    case 'ratio': return ratio(value, leaf.digits ?? 1)
    // index_weight is published as a percentage rather than a fraction, unlike
    // every other rate in the dataset, so it is divided back down before
    // percent() multiplies it up again. See the README's deferred note.
    case 'weight': return percent(value / 100, leaf.digits ?? 3)
    default: return String(value)
  }
}

function numericCell(leaf: Leaf) {
  return function Cell({ row }: { row: { original: Stock } }) {
    const value = row.original[leaf.id] as number | null
    if (value == null) {
      return <span className="empty" title={explainMissing(row.original, leaf.id)}>{EMPTY}</span>
    }
    const cls = leaf.signed ? (value >= 0 ? 'pos' : 'neg') : leaf.className
    if (leaf.derivedFlag && row.original[leaf.derivedFlag]) {
      return (
        <span
          className={cls ? `${cls} derived` : 'derived'}
          title={'Computed from market cap and the last annual net income, '
            + 'because the data source publishes no EPS for this listing. '
            + 'An annual figure, not a trailing twelve months.'}
        >
          {formatNumber(leaf, value)}
        </span>
      )
    }
    return <span className={cls}>{formatNumber(leaf, value)}</span>
  }
}

function textCell(leaf: Leaf) {
  return function Cell({ row }: { row: { original: Stock } }) {
    if (leaf.kind === 'source') {
      const source = row.original.fundamentals_source
      return <span className={`source source-${source}`} title={sourceLabel(source)}>{source}</span>
    }
    const raw = row.original[leaf.id] as string | null
    // Company names are ellipsised by CSS, so the full name lives in a tooltip.
    return <span className={leaf.className} title={raw ?? undefined}>{plain(raw)}</span>
  }
}

function leafColumn(leaf: Leaf): ColumnDef<Stock, any> {  // eslint-disable-line @typescript-eslint/no-explicit-any
  const text = isTextKind(leaf.kind)
  return col.accessor(text ? str(leaf.id) : num(leaf.id), {
    id: leaf.id,
    header: leaf.header,
    size: leaf.size ?? SIZE_BY_KIND[leaf.kind],
    sortUndefined: 'last',
    sortDescFirst: !text,
    meta: { align: text ? 'left' : 'right', group: leaf.group, help: leaf.help },
    cell: text ? textCell(leaf) : numericCell(leaf),
  }) as ColumnDef<Stock, any>  // eslint-disable-line @typescript-eslint/no-explicit-any
}

/**
 * Turn the flat leaf registry into the two-tier tree TanStack renders.
 *
 * Each run of consecutive leaves sharing a group becomes one banner spanning
 * them; spine leaves carry `NO_GROUP` and are emitted bare, so they occupy the
 * lower header row with an empty cell above.
 */
export function buildColumns(leaves: Leaf[]): ColumnDef<Stock, any>[] {  // eslint-disable-line @typescript-eslint/no-explicit-any
  const out: ColumnDef<Stock, any>[] = []  // eslint-disable-line @typescript-eslint/no-explicit-any
  let i = 0
  while (i < leaves.length) {
    const group = leaves[i].group
    if (group === NO_GROUP) {
      out.push(leafColumn(leaves[i]))
      i += 1
      continue
    }
    const run: Leaf[] = []
    while (i < leaves.length && leaves[i].group === group) {
      run.push(leaves[i])
      i += 1
    }
    out.push(col.group({
      id: group,
      header: group,
      columns: run.map(leafColumn),
    }) as ColumnDef<Stock, any>)  // eslint-disable-line @typescript-eslint/no-explicit-any
  }
  return out
}

export const columns = buildColumns(LEAVES)
