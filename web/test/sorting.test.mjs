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
