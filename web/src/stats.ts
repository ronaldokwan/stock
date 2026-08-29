/**
 * Benchmark statistics for the screener.
 *
 * A median, never a mean. These distributions are right-skewed with unbounded
 * tails — trailing P/E runs to 4368, price/sales to 3329 — so the mean sits
 * roughly 1.8-3.1x above the median across the valuation columns. EV/EBITDA
 * settles the argument on its own: loss-making companies drag its mean
 * *negative*, and "average EV/EBITDA -9.4x" is worse than no number at all.
 *
 * Sector-relative, because a universe-wide figure inverts the answer. Samsung's
 * P/E of 38.1 reads 69% expensive against the universe median of 22.6, but
 * Samsung is Technology, whose median is 45.0 — cheap against its actual peers.
 *
 * Pure functions with no JSX and no runtime imports, so the test suite can load
 * this under `node --experimental-strip-types`, the same way it loads
 * `columns.def.ts`.
 */
import type { Stock } from './types'
import { AGGREGATE_BY_KIND, type Leaf } from './columns.def.ts'

/**
 * Fewest values that can carry a median.
 *
 * Only 4 of the 286 sector-by-column pairs fall below this — Energy's 5 values
 * for five-year profit growth, Real Estate's 8 for ten-year revenue growth — so
 * it rarely fires. But a median of five is not a benchmark, and publishing one
 * as if it were would be the same mistake as treating a null as a zero.
 */
export const MIN_SAMPLE = 10

export interface Summary {
  value: number
  /** How many rows actually carried a value. Always disclosed in the UI. */
  n: number
}

/** Non-null numbers only. A gap is never coerced to zero. */
function present(values: (number | null | undefined)[]): number[] {
  return values.filter((v): v is number => v != null && Number.isFinite(v))
}

export function median(
  values: (number | null | undefined)[],
  minN: number = MIN_SAMPLE,
): Summary | null {
  const found = present(values).sort((a, b) => a - b)
  if (found.length < minN) return null
  const mid = Math.floor(found.length / 2)
  const value = found.length % 2 === 0
    ? (found[mid - 1] + found[mid]) / 2
    : found[mid]
  return { value, n: found.length }
}

export function total(
  values: (number | null | undefined)[],
  minN: number = 1,
): Summary | null {
  const found = present(values)
  if (found.length < minN) return null
  return { value: found.reduce((sum, v) => sum + v, 0), n: found.length }
}

/** The aggregate a leaf publishes, honouring a per-column override. */
export function aggregateFor(leaf: Leaf) {
  return leaf.aggregate ?? AGGREGATE_BY_KIND[leaf.kind]
}

/**
 * One summary per column, over the rows currently on screen.
 *
 * Deliberately computed from the filtered set rather than the whole universe:
 * filtering to Technology then yields Technology's medians without the reader
 * having to learn a second concept, and the row always describes exactly what
 * is above it.
 */
export function summarise(rows: Stock[], leaves: Leaf[]): Record<string, Summary | null> {
  const out: Record<string, Summary | null> = {}
  for (const leaf of leaves) {
    const kind = aggregateFor(leaf)
    if (kind === 'none') continue
    const values = rows.map((r) => r[leaf.id] as number | null)
    out[leaf.id] = kind === 'total' ? total(values) : median(values)
  }
  return out
}

function groupBySector(rows: Stock[]): Map<string, Stock[]> {
  const out = new Map<string, Stock[]>()
  for (const row of rows) {
    if (!row.sector) continue           // one row in the universe has no sector
    const bucket = out.get(row.sector)
    if (bucket) bucket.push(row)
    else out.set(row.sector, [row])
  }
  return out
}

/**
 * Median per sector per column, for the peer comparison in the detail drawer.
 *
 * Every column that carries a median gets one, including those a `total`
 * aggregates in the summary row: a sector's *typical* market cap is a
 * meaningful peer figure even though the summary row shows a sum.
 */
export function sectorMedians(rows: Stock[], leaves: Leaf[]): Record<string, Record<string, Summary | null>> {
  const out: Record<string, Record<string, Summary | null>> = {}
  for (const [sector, members] of groupBySector(rows)) {
    const stats: Record<string, Summary | null> = {}
    for (const leaf of leaves) {
      if (aggregateFor(leaf) === 'none') continue
      stats[leaf.id] = median(members.map((r) => r[leaf.id] as number | null))
    }
    out[sector] = stats
  }
  return out
}

/**
 * How favourable a value is within its sector, 0 (worst) to 1 (best).
 *
 * Separated from the percentile itself so the raw position stays available for
 * the tooltip: the reader is told where the value sits, and the colour tells
 * them what that means for this particular metric.
 */
export function goodness(pct: number, goodWhen: 'high' | 'low'): number {
  return goodWhen === 'low' ? 1 - pct : pct
}

/**
 * Where a value sits within its sector, 0 (lowest) to 1 (highest).
 *
 * Percentile rather than distance from the median: a z-score computed on a
 * distribution whose maximum is 4368 tells the reader nothing, while a
 * percentile is outlier-immune and directly interpretable. Ties share the
 * midpoint of the range they span, so equal values always colour identically.
 */
export function sectorPercentiles(
  rows: Stock[],
  leaves: Leaf[],
): Map<string, Record<string, number>> {
  const out = new Map<string, Record<string, number>>()
  // Only columns with an agreed better end are ranked. The valuation multiples,
  // dividend yield and beta have none, so they are never shaded and there is
  // nothing to compute for them.
  const ranked = leaves.filter((leaf) => leaf.goodWhen != null)

  for (const [, members] of groupBySector(rows)) {
    for (const leaf of ranked) {
      const found = members
        .map((r) => ({ symbol: r.symbol, value: r[leaf.id] as number | null }))
        .filter((e): e is { symbol: string; value: number } =>
          e.value != null && Number.isFinite(e.value))
      if (found.length < MIN_SAMPLE) continue

      found.sort((a, b) => a.value - b.value)
      const last = found.length - 1
      let i = 0
      while (i < found.length) {
        let j = i
        while (j + 1 < found.length && found[j + 1].value === found[i].value) j += 1
        // Midpoint of the tied run, so equal values never colour differently.
        const pct = last === 0 ? 0.5 : ((i + j) / 2) / last
        for (let k = i; k <= j; k += 1) {
          const row = out.get(found[k].symbol) ?? {}
          row[leaf.id] = pct
          out.set(found[k].symbol, row)
        }
        i = j + 1
      }
    }
  }
  return out
}
