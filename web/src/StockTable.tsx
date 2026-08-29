import { useEffect, useRef } from 'react'
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { columns } from './columns'
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

export function StockTable({
  data, sorting, onSortingChange, columnVisibility,
  onRowClick, onVisibleRowsChange,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)

  const table = useReactTable({
    data,
    columns,
    state: { sorting, columnVisibility },
    onSortingChange,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    enableSortingRemoval: true,
    enableMultiSort: true,
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
                const sorted = header.column.getIsSorted()
                const index = sorting.findIndex((s) => s.id === header.column.id)
                return (
                  <th
                    key={header.id}
                    className={meta?.align === 'right' ? 'right' : undefined}
                    style={{ width: header.getSize() }}
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
                  return (
                    <td key={cell.id} className={meta?.align === 'right' ? 'right' : undefined}>
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
