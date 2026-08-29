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
import { columns } from './columns'
import { SPINE } from './columns.def'
import type { Stock } from './types'

interface Props {
  data: Stock[]
  sorting: SortingState
  onSortingChange: (updater: SortingState | ((old: SortingState) => SortingState)) => void
  columnVisibility: VisibilityState
  onRowClick: (row: Stock) => void
  onVisibleRowsChange?: (rows: Stock[]) => void
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

function classes(...names: (string | false | undefined)[]): string | undefined {
  const out = names.filter(Boolean).join(' ')
  return out || undefined
}

export function StockTable({
  data, sorting, onSortingChange, columnVisibility,
  onRowClick, onVisibleRowsChange,
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
                  return (
                    <td
                      key={cell.id}
                      className={classes(
                        meta?.align === 'right' && 'right',
                        left !== undefined && 'pinned',
                        cell.column.id === EDGE && 'edge',
                      )}
                      style={{ left }}
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
      </table>
    </div>
  )
}
