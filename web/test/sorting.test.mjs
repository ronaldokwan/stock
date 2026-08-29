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
