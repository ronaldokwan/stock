import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SortingState, VisibilityState } from '@tanstack/react-table'
import { ALL_COLUMN_IDS, resolvePreset } from './columns.def'
import { applyFilters, EMPTY_FILTERS, Filters, type FilterState } from './Filters'
import { DetailDrawer } from './DetailDrawer'
import { StockTable } from './StockTable'
import { toCSV } from './format'
import type { Meta, Sparklines, Stock } from './types'

const BASE = import.meta.env.BASE_URL

function visibilityFor(preset: string): VisibilityState {
  const wanted = new Set(resolvePreset(preset))
  return Object.fromEntries(ALL_COLUMN_IDS.map((id) => [id, wanted.has(id)]))
}

function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    try {
      const saved = localStorage.getItem('theme')
      if (saved === 'light' || saved === 'dark') return saved
    } catch { /* private mode or blocked storage */ }
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('theme', theme) } catch { /* ignore */ }
  }, [theme])
  return [theme, setTheme] as const
}

export default function App() {
  const [stocks, setStocks] = useState<Stock[] | null>(null)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [sparks, setSparks] = useState<Sparklines>({})
  const [error, setError] = useState<string | null>(null)

  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS)
  const [preset, setPreset] = useState('Overview')
  const [sorting, setSorting] = useState<SortingState>([{ id: 'rank', desc: false }])
  const [selected, setSelected] = useState<Stock | null>(null)
  const [theme, setTheme] = useTheme()

  const visibleRows = useRef<Stock[]>([])
  // Stable identity so the table's reporting effect does not re-fire each render.
  const handleVisibleRows = useCallback((rows: Stock[]) => {
    visibleRows.current = rows
  }, [])

  useEffect(() => {
    Promise.all([
      fetch(`${BASE}data/stocks.json`).then((r) => {
        if (!r.ok) throw new Error(`stocks.json: HTTP ${r.status}`)
        return r.json()
      }),
      fetch(`${BASE}data/meta.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ])
      .then(([rows, m]) => { setStocks(rows); setMeta(m) })
      .catch((e: Error) => setError(e.message))

    // Sparklines are only needed once a row is opened, so they load separately.
    fetch(`${BASE}data/sparklines.json`)
      .then((r) => (r.ok ? r.json() : {}))
      .then(setSparks)
      .catch(() => setSparks({}))
  }, [])

  const columnVisibility = useMemo(() => visibilityFor(preset), [preset])
  const filtered = useMemo(
    () => (stocks ? applyFilters(stocks, filters) : []),
    [stocks, filters],
  )

  function exportCSV() {
    const cols = resolvePreset(preset)
    const rows = visibleRows.current.length ? visibleRows.current : filtered
    const blob = new Blob([toCSV(rows, cols)], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `stocks-${preset.toLowerCase()}-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (error) {
    return (
      <main className="state">
        <h1>Could not load the data</h1>
        <p className="muted">{error}</p>
        <p className="muted">
          If you are running locally, generate the dataset first:
          <code>python -m pipeline.run</code>
        </p>
      </main>
    )
  }

  if (!stocks) {
    return <main className="state"><p className="muted">Loading 1,000 stocks…</p></main>
  }

  const generated = meta?.generated_at
    ? new Date(meta.generated_at).toLocaleString(undefined,
        { dateStyle: 'medium', timeStyle: 'short' })
    : null
  const secCount = meta?.fundamentals_breakdown?.sec ?? 0

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <h1>Global Top 1000 Stocks</h1>
          <p className="muted">
            The largest listed companies worldwide, ranked by market capitalisation.
            {generated && <> Data as of <strong>{generated}</strong>.</>}
          </p>
        </div>
        <button
          className="theme"
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          aria-label="Toggle colour theme"
        >
          {theme === 'dark' ? '☀' : '☾'}
        </button>
      </header>

      <Filters
        all={stocks}
        filters={filters}
        onChange={setFilters}
        preset={preset}
        onPreset={setPreset}
        visibleCount={filtered.length}
        onExport={exportCSV}
      />

      <StockTable
        data={filtered}
        sorting={sorting}
        onSortingChange={setSorting}
        columnVisibility={columnVisibility}
        onRowClick={setSelected}
        onVisibleRowsChange={handleVisibleRows}
      />

      <DetailDrawer
        stock={selected}
        spark={selected ? sparks[selected.symbol] : undefined}
        onClose={() => setSelected(null)}
      />

      <footer className="sitefoot">
        <p>
          Click a column to sort, shift-click to add a second sort. Click a row for detail.
          A dash (—) means the value is genuinely unavailable, not zero — hover it for the reason.
        </p>
        {meta && (
          <p className="muted">
            Universe from {meta.universe_source}. Prices from {meta.price_source}.
            Revenue and profit history for {secCount} companies from SEC EDGAR XBRL
            filings; the rest fall back to roughly four years from Yahoo.
            {' '}20-year growth is available as share-price return only — no free
            source provides 20 years of global fundamentals.
            {meta.derived_pe_rows > 0 && (
              <> P/E is computed from market cap and annual net income for{' '}
                {meta.derived_pe_rows} listings the price source publishes no EPS
                for; those cells are underlined.</>
            )}
          </p>
        )}
      </footer>
    </>
  )
}
