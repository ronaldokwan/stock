import { useEffect, useMemo, useRef } from 'react'
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type Column,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { columns, formatNumber } from './columns'
import { LEAF_BY_ID, SPINE } from './columns.def'
import { EMPTY } from './format'
import { aggregateFor, MIN_SAMPLE, type Summary } from './stats'
import type { Stock } from './types'

interface Props {
  data: Stock[]
  sorting: SortingState
  onSortingChange: (updater: SortingState | ((old: SortingState) => SortingState)) => void
  columnVisibility: VisibilityState
  onRowClick: (row: Stock) => void
  onVisibleRowsChange?: (rows: Stock[]) => void
  /** Benchmark per column, over the filtered rows. */
  summary: Record<string, Summary | null>
  /** Each row's percentile within its sector, keyed by symbol then column. */
  percentiles: Map<string, Record<string, number>>
  heat: boolean
}

const ROW_HEIGHT = 34

/**
 * Sticky offset for a pinned column, or undefined when it is not pinned.
 *
 * `getStart` accumulates the widths of the pinned columns to the left, so the
 * three identity columns stack against the left edge instead of overlapping.
 */
function pinnedLeft(column: Column<Stock, unknown>): number | undefined {
  return column.getIsPinned() === 'left' ? column.getStart('left') : undefined
}

/** The rightmost frozen column, which carries the border marking the freeze. */
const EDGE = SPINE[SPINE.length - 1]

/** The frozen column the summary row labels itself in. */
const LABEL_COLUMN = SPINE[SPINE.length - 1]

function isTextColumn(leaf: { kind: string }): boolean {
  return leaf.kind === 'text' || leaf.kind === 'source'
}

function noStatReason(rowCount: number): string {
  return rowCount < MIN_SAMPLE
    ? `Only ${rowCount} row${rowCount === 1 ? '' : 's'} in view — too few for a median.`
    : `Fewer than ${MIN_SAMPLE} rows carry this value, so no median is shown.`
}

function classes(...names: (string | false | undefined)[]): string | undefined {
  const out = names.filter(Boolean).join(' ')
  return out || undefined
}

/**
 * Background shade for one cell, from its percentile within its own sector.
 *
 * Deliberately not the green/red the signed columns already use: those mean
 * "positive/negative return", and reusing them here would make one colour carry
 * two meanings in the same row. Blue reads below the sector median and amber
 * above it, with no implication that either is good.
 */
function heatClass(pct: number | undefined): { className?: string; intensity?: number } {
  if (pct == null) return {}
  const intensity = Math.abs(pct - 0.5) * 2
  if (intensity < 0.02) return {}
  return { className: pct < 0.5 ? 'heat heat-below' : 'heat heat-above', intensity }
}

export function StockTable({
  data, sorting, onSortingChange, columnVisibility,
  onRowClick, onVisibleRowsChange, summary, percentiles, heat,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Static, but memoised so it does not remount the table on every render.
  const columnPinning = useMemo(() => ({ left: [...SPINE], right: [] }), [])

  const table = useReactTable({
    data,
    columns,
    state: { sorting, columnVisibility, columnPinning },
    onSortingChange,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    enableSortingRemoval: true,
    enableMultiSort: true,
    enableColumnPinning: true,
    isMultiSortEvent: (e) => (e as unknown as MouseEvent).shiftKey,
  })

  const rows = table.getRowModel().rows

  useEffect(() => {
    onVisibleRowsChange?.(rows.map((r) => r.original))
  }, [rows, onVisibleRowsChange])

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  })
  const virtualRows = virtualizer.getVirtualItems()
  const paddingTop = virtualRows[0]?.start ?? 0
  const paddingBottom =
    virtualizer.getTotalSize() - (virtualRows[virtualRows.length - 1]?.end ?? 0)

  if (rows.length === 0) {
    return (
      <div className="table-scroll">
        <p className="no-results">No stocks match these filters.</p>
      </div>
    )
  }

  return (
    <div className="table-scroll" ref={scrollRef}>
      <table>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                const meta = header.column.columnDef.meta as
                  | { align?: string; help?: string } | undefined
                const isBanner = header.subHeaders.length > 0
                const left = pinnedLeft(header.column)

                // A pinned leaf occupies the lower row and leaves a placeholder
                // above it. That cell must still be sticky, or the frozen
                // corner turns transparent when the body scrolls under it.
                if (header.isPlaceholder) {
                  return (
                    <th
                      key={header.id}
                      className={classes(
                        'spacer',
                        left !== undefined && 'pinned',
                        header.column.id === EDGE && 'edge',
                      )}
                      style={{ width: header.getSize(), left }}
                      aria-hidden
                    />
                  )
                }

                if (isBanner) {
                  return (
                    <th
                      key={header.id}
                      colSpan={header.colSpan}
                      className="banner"
                      style={{ width: header.getSize() }}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  )
                }

                const sorted = header.column.getIsSorted()
                const index = sorting.findIndex((s) => s.id === header.column.id)
                return (
                  <th
                    key={header.id}
                    colSpan={header.colSpan}
                    className={classes(
                      meta?.align === 'right' && 'right',
                      left !== undefined && 'pinned',
                      header.column.id === EDGE && 'edge',
                    )}
                    style={{ width: header.getSize(), left }}
                    onClick={header.column.getToggleSortingHandler()}
                    title={meta?.help ?? 'Click to sort. Shift-click to add a second sort.'}
                    aria-sort={
                      sorted === 'asc' ? 'ascending'
                        : sorted === 'desc' ? 'descending' : 'none'
                    }
                  >
                    <span className="th-inner">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      <span className="sort-mark">
                        {sorted === 'asc' ? '▲' : sorted === 'desc' ? '▼' : ''}
                        {sorting.length > 1 && index >= 0 && (
                          <sup className="sort-order">{index + 1}</sup>
                        )}
                      </span>
                    </span>
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {paddingTop > 0 && <tr style={{ height: paddingTop }} aria-hidden />}
          {virtualRows.map((vr) => {
            const row = rows[vr.index]
            return (
              <tr
                key={row.id}
                onClick={() => onRowClick(row.original)}
                className={row.original.stale ? 'stale' : undefined}
                style={{ height: ROW_HEIGHT }}
              >
                {row.getVisibleCells().map((cell) => {
                  const meta = cell.column.columnDef.meta as { align?: string } | undefined
                  const left = pinnedLeft(cell.column)
                  const shade = heat
                    ? heatClass(percentiles.get(row.original.symbol)?.[cell.column.id])
                    : {}
                  return (
                    <td
                      key={cell.id}
                      className={classes(
                        meta?.align === 'right' && 'right',
                        left !== undefined && 'pinned',
                        cell.column.id === EDGE && 'edge',
                        shade.className,
                      )}
                      style={shade.intensity === undefined
                        ? { left }
                        : { left, ['--heat' as string]: shade.intensity.toFixed(2) }}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  )
                })}
              </tr>
            )
          })}
          {paddingBottom > 0 && <tr style={{ height: paddingBottom }} aria-hidden />}
        </tbody>
        <tfoot>
          <tr>
            {table.getVisibleLeafColumns().map((column) => {
              const leaf = LEAF_BY_ID[column.id]
              const left = pinnedLeft(column)
              const shared = {
                className: classes(
                  leaf && !isTextColumn(leaf) && 'right',
                  left !== undefined && 'pinned',
                  column.id === EDGE && 'edge',
                ),
                style: { left },
              }

              // The label sits in the widest frozen column, where it stays put
              // while the numbers it describes scroll past. It says only how
              // many rows, because the columns are not all the same statistic:
              // most are medians, market cap and index weight are totals.
              if (column.id === LABEL_COLUMN) {
                return (
                  <td
                    key={column.id}
                    {...shared}
                    className={classes(shared.className, 'foot-label')}
                    title={'Median per column, over the rows currently shown. '
                      + 'Market cap and index weight are totals; price carries no '
                      + 'statistic, because the column mixes listing currencies.'}
                  >
                    {`${data.length.toLocaleString()} row${data.length === 1 ? '' : 's'} shown`}
                  </td>
                )
              }

              const stat = summary[column.id]
              if (!leaf || stat == null) {
                return (
                  <td key={column.id} {...shared}>
                    {leaf && aggregateFor(leaf) !== 'none' && (
                      <span className="empty" title={noStatReason(data.length)}>{EMPTY}</span>
                    )}
                  </td>
                )
              }
              const kind = aggregateFor(leaf)
              return (
                <td
                  key={column.id}
                  {...shared}
                  title={`${kind === 'total' ? 'Total' : 'Median'} of ${stat.n.toLocaleString()} `
                    + `value${stat.n === 1 ? '' : 's'}`
                    + (stat.n < data.length
                      ? ` — ${(data.length - stat.n).toLocaleString()} rows have none.`
                      : '.')}
                >
                  {formatNumber(leaf, stat.value)}
                </td>
              )
            })}
          </tr>
        </tfoot>
      </table>
    </div>
  )
}
