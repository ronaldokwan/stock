/**
 * Sorting correctness check for the screener table.
 *
 * The rule that matters: a missing value must sort to the BOTTOM in both
 * directions. If nulls were treated as zero, a company with no 20-year history
 * would rank alongside one that genuinely returned 0% a year — which would be
 * actively misleading rather than merely untidy.
 *
 * Run with:  node src/sorting.test.mjs
 */
import assert from 'node:assert/strict'
import {
  createColumnHelper,
  getCoreRowModel,
  getSortedRowModel,
  createTable,
} from '@tanstack/react-table'

const col = createColumnHelper()

const data = [
  { symbol: 'HIGH', return_20y: 0.30 },
  { symbol: 'ZERO', return_20y: 0.0 },
  { symbol: 'NONE', return_20y: null },
  { symbol: 'LOW', return_20y: -0.10 },
  { symbol: 'NONE2', return_20y: null },
]

const columns = [
  col.accessor('symbol', { id: 'symbol' }),
  col.accessor((r) => r.return_20y ?? undefined, {
    id: 'return_20y',
    sortUndefined: 'last',
  }),
]

function order(sorting) {
  let state = { sorting }
  const table = createTable({
    data,
    columns,
    state,
    onStateChange: () => {},
    renderFallbackValue: null,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })
  return table.getRowModel().rows.map((r) => r.original.symbol)
}

const desc = order([{ id: 'return_20y', desc: true }])
const asc = order([{ id: 'return_20y', desc: false }])

console.log('descending:', desc.join(' '))
console.log('ascending :', asc.join(' '))

// Nulls last in BOTH directions.
assert.deepEqual(desc.slice(-2).sort(), ['NONE', 'NONE2'],
  'descending sort must place missing values last')
assert.deepEqual(asc.slice(-2).sort(), ['NONE', 'NONE2'],
  'ascending sort must place missing values last')

// Real values still ordered correctly, and 0 is not confused with missing.
assert.deepEqual(desc.slice(0, 3), ['HIGH', 'ZERO', 'LOW'],
  'descending: 0.30 > 0.0 > -0.10')
assert.deepEqual(asc.slice(0, 3), ['LOW', 'ZERO', 'HIGH'],
  'ascending: -0.10 < 0.0 < 0.30')

console.log('\nPASS - missing values sort last in both directions, and 0 is ranked as a real value.')

/* ------------------------------------------------------------------------ */
/* Column registry integrity.
 *
 * The block above builds its own throwaway table, so it cannot notice a
 * regression in the real column definitions. These assertions run against the
 * shipped registry, and cover the failure mode of commit 7489ab5: a preset
 * naming an id the table does not define silently fails to hide anything, so
 * columns from the previous preset stay on screen.
 */
import {
  ALL_COLUMN_IDS, GROUPS, LEAVES, NO_GROUP, PRESETS, SPINE, resolvePreset,
} from '../src/columns.def.ts'

const ids = new Set(ALL_COLUMN_IDS)

assert.equal(ids.size, ALL_COLUMN_IDS.length, 'column ids must be unique')

for (const [name, def] of Object.entries(PRESETS)) {
  const resolved = resolvePreset(name)

  for (const id of resolved) {
    assert.ok(ids.has(id), `preset ${name} names unknown column "${id}"`)
  }
  for (const id of SPINE) {
    assert.ok(resolved.includes(id), `preset ${name} is missing spine column "${id}"`)
  }
  for (const group of def.groups ?? []) {
    assert.ok(GROUPS.includes(group), `preset ${name} names unknown group "${group}"`)
  }
  assert.equal(new Set(resolved).size, resolved.length,
    `preset ${name} resolved to duplicate columns`)

  // Display order, so an exported CSV matches the table it came from.
  const order = resolved.map((id) => ALL_COLUMN_IDS.indexOf(id))
  assert.deepEqual(order, [...order].sort((a, b) => a - b),
    `preset ${name} must resolve in display order`)
}

// A group's leaves must be contiguous, or buildColumns emits two banners for it.
const runs = []
for (const leaf of LEAVES) {
  if (runs.at(-1) !== leaf.group) runs.push(leaf.group)
}
const banners = runs.filter((g) => g !== NO_GROUP)
assert.equal(new Set(banners).size, banners.length,
  'each group must occupy one contiguous run of leaves')

// Every spine column exists, and none of them sits inside a group.
for (const id of SPINE) {
  const leaf = LEAVES.find((l) => l.id === id)
  assert.ok(leaf, `spine column "${id}" is not defined`)
  assert.equal(leaf.group, NO_GROUP, `spine column "${id}" must not be grouped`)
}

console.log(`PASS - ${ALL_COLUMN_IDS.length} columns, ${GROUPS.length} groups, ` +
  `${Object.keys(PRESETS).length} presets resolve cleanly.`)

/* ------------------------------------------------------------------------ */
/* Benchmark statistics.
 *
 * The rule from the sort test carries into the aggregate: a missing value is
 * not a zero. A median that silently counted nulls as zeros would drag every
 * low-coverage column toward zero and read as a real, defensible number.
 */
import { AGGREGATE_BY_KIND, LEAF_BY_ID, SIZE_BY_KIND } from '../src/columns.def.ts'
import { MIN_SAMPLE, median, total, goodness, sectorPercentiles } from '../src/stats.ts'

const enough = (extra = []) => [...Array(MIN_SAMPLE).fill(1), ...extra]

// Odd and even counts.
assert.equal(median([...Array(11).keys()]).value, 5, 'odd count takes the middle')
assert.equal(median([...Array(10).keys()]).value, 4.5, 'even count averages the pair')

// Nulls are excluded, never zeroed. With 10 ones and two nulls the median is 1;
// were the nulls counted as zeros it would fall to 0.5.
const withGaps = median([...Array(10).fill(1), null, undefined], 10)
assert.equal(withGaps.value, 1, 'nulls must not be treated as zero')
assert.equal(withGaps.n, 10, 'n counts only the values that were present')

// Order does not matter, and ties are handled.
assert.equal(median([5, 1, 3, 2, 4, 9, 8, 7, 6, 0]).value, 4.5)
assert.equal(median(Array(12).fill(7)).value, 7, 'all-equal values median to that value')

// Too few to be honest.
assert.equal(median([1, 2, 3]), null, `fewer than ${MIN_SAMPLE} values yields no median`)
assert.equal(median([1, 2, 3], 3).value, 2, 'the threshold is overridable')
assert.equal(median([]), null)
assert.equal(median([null, null], 1), null, 'a column of nulls has no median')

// Totals ignore gaps too.
assert.equal(total([1, 2, null, 3]).value, 6)
assert.equal(total([1, 2, null, 3]).n, 3)
assert.equal(total([]), null)

// The column that must never be averaged: price mixes 29 listing currencies.
assert.equal(AGGREGATE_BY_KIND.price, 'none',
  'price holds many currencies at once and must carry no aggregate')
assert.equal(AGGREGATE_BY_KIND.int, 'none', 'rank is ordinal')
assert.equal(AGGREGATE_BY_KIND.text, 'none')
assert.equal(AGGREGATE_BY_KIND.source, 'none')
assert.equal(AGGREGATE_BY_KIND.money, 'total', 'market cap sums rather than medians')
assert.equal(AGGREGATE_BY_KIND.ratio, 'median')
assert.equal(AGGREGATE_BY_KIND.percent, 'median')

// Every kind is covered by both per-kind maps, so a new kind cannot slip
// through with an undefined width or an accidental aggregate.
for (const kind of Object.keys(SIZE_BY_KIND)) {
  assert.ok(AGGREGATE_BY_KIND[kind] !== undefined,
    `kind "${kind}" has no aggregate declared`)
}

// Percentiles: bounded, monotonic, and stable across ties.
{
  // A directional leaf: only those are ranked, so the mechanics test needs one.
  const leaf = { id: 'profit_margin', kind: 'percent', group: 'Quality',
                 header: 'Margin', goodWhen: 'high' }
  const rows = [...Array(12).keys()].map((i) => ({
    symbol: `S${i}`, sector: 'Tech', profit_margin: i,
  }))
  const pct = sectorPercentiles(rows, [leaf])
  const values = rows.map((r) => pct.get(r.symbol).profit_margin)

  assert.equal(values[0], 0, 'lowest value sits at 0')
  assert.equal(values[11], 1, 'highest value sits at 1')
  for (const v of values) assert.ok(v >= 0 && v <= 1, 'percentiles stay in range')
  for (let i = 1; i < values.length; i += 1) {
    assert.ok(values[i] > values[i - 1], 'percentile rises with value')
  }

  const tied = [...Array(12).keys()].map((i) => ({
    symbol: `T${i}`, sector: 'Tech', profit_margin: i < 6 ? 10 : 20,
  }))
  const tpct = sectorPercentiles(tied, [leaf])
  assert.equal(tpct.get('T0').profit_margin, tpct.get('T5').profit_margin,
    'tied values must share a percentile')

  // A sector too thin to median is also too thin to rank.
  const thin = [...Array(4).keys()].map((i) => ({
    symbol: `U${i}`, sector: 'Tiny', profit_margin: i,
  }))
  assert.equal(sectorPercentiles(thin, [leaf]).size, 0,
    `a sector with fewer than ${MIN_SAMPLE} values gets no percentiles`)

  // A row with no sector has no peers and must not crash the pass.
  assert.doesNotThrow(() =>
    sectorPercentiles([{ symbol: 'X', sector: null, profit_margin: 5 }], [leaf]))
}

/* Shading direction.
 *
 * Sector shading states that one end of a measure is better, so it may only be
 * applied where that is actually agreed. These assertions pin the metrics that
 * must stay unshaded: for dividend yield in particular, the top quintile of
 * this universe returned 7.7% a year over ten years against the bottom
 * quintile's 24.2%, while falling less far in a drawdown — a trade-off, not a
 * ranking.
 */
for (const id of ['dividend_yield', 'trailing_pe', 'forward_pe', 'price_to_book',
                  'price_to_sales', 'ev_to_ebitda', 'beta', 'market_cap_usd',
                  'index_weight', 'pct_from_52w_high']) {
  assert.equal(LEAF_BY_ID[id].goodWhen, undefined,
    `"${id}" has no agreed better end and must never be shaded`)
}

// Metrics that do have one, in both directions.
for (const id of ['profit_margin', 'return_on_equity', 'return_10y',
                  'revenue_cagr_5y', 'net_income_cagr_5y', 'max_drawdown']) {
  assert.equal(LEAF_BY_ID[id].goodWhen, 'high', `"${id}" is better when higher`)
}
for (const id of ['debt_to_equity', 'volatility_5y']) {
  assert.equal(LEAF_BY_ID[id].goodWhen, 'low', `"${id}" is better when lower`)
}

// Max drawdown is stored negative, so "less bad" is the higher number.
assert.equal(LEAF_BY_ID.max_drawdown.goodWhen, 'high',
  'a -30% drawdown is better than -70%, so higher is the better end')

// goodness() inverts for lower-is-better, leaving the median neutral either way.
const close = (a, b) => Math.abs(a - b) < 1e-9
assert.ok(close(goodness(0.9, 'high'), 0.9))
assert.ok(close(goodness(0.9, 'low'), 0.1), 'a high debt ratio is the worse end')
assert.equal(goodness(0.5, 'high'), goodness(0.5, 'low'), 'the median is neutral')

// Only directional columns get percentiles at all — nothing to shade otherwise.
{
  const rows = [...Array(12).keys()].map((i) => ({
    symbol: `Y${i}`, sector: 'Tech', dividend_yield: i / 100, profit_margin: i / 100,
  }))
  const pct = sectorPercentiles(rows, [LEAF_BY_ID.dividend_yield, LEAF_BY_ID.profit_margin])
  assert.equal(pct.get('Y0').dividend_yield, undefined,
    'dividend yield must produce no percentile to shade with')
  assert.equal(pct.get('Y0').profit_margin, 0, 'profit margin still ranks')
}

console.log('PASS - medians ignore gaps, price carries no aggregate, '
  + 'percentiles are bounded and tie-stable.')
console.log('PASS - shading only where a better end is agreed; '
  + 'yield, P/E and beta stay unshaded.')
