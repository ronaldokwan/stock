import { useMemo } from 'react'
import type { Stock } from './types'
import { PRESETS } from './columns.def'

export interface FilterState {
  search: string
  country: string
  sector: string
  minCapB: number
  source: string
}

export const EMPTY_FILTERS: FilterState = {
  search: '', country: '', sector: '', minCapB: 0, source: '',
}

interface Props {
  all: Stock[]
  filters: FilterState
  onChange: (next: FilterState) => void
  preset: string
  onPreset: (name: string) => void
  visibleCount: number
  onExport: () => void
  heat: boolean
  onHeat: (on: boolean) => void
}

function uniqueSorted(rows: Stock[], key: 'country' | 'sector'): string[] {
  return [...new Set(rows.map((r) => r[key]).filter((v): v is string => !!v))].sort()
}

export function Filters({
  all, filters, onChange, preset, onPreset, visibleCount, onExport, heat, onHeat,
}: Props) {
  const countries = useMemo(() => uniqueSorted(all, 'country'), [all])
  const sectors = useMemo(() => uniqueSorted(all, 'sector'), [all])
  const set = <K extends keyof FilterState>(key: K, value: FilterState[K]) =>
    onChange({ ...filters, [key]: value })

  const dirty =
    filters.search !== '' || filters.country !== '' || filters.sector !== '' ||
    filters.minCapB > 0 || filters.source !== ''

  return (
    <div className="filters">
      <input
        className="search"
        type="search"
        placeholder="Search company or ticker…"
        value={filters.search}
        onChange={(e) => set('search', e.target.value)}
        aria-label="Search company or ticker"
      />

      <select value={filters.country} onChange={(e) => set('country', e.target.value)}
              aria-label="Filter by country">
        <option value="">All countries</option>
        {countries.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>

      <select value={filters.sector} onChange={(e) => set('sector', e.target.value)}
              aria-label="Filter by sector">
        <option value="">All sectors</option>
        {sectors.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>

      <label className="cap-filter">
        Min cap
        <input
          type="range" min={0} max={500} step={10}
          value={filters.minCapB}
          onChange={(e) => set('minCapB', Number(e.target.value))}
          aria-label="Minimum market cap in billions of dollars"
        />
        <span className="cap-value">${filters.minCapB}B</span>
      </label>

      <select value={filters.source} onChange={(e) => set('source', e.target.value)}
              aria-label="Filter by fundamentals source"
              title="Where each row's revenue and profit history comes from">
        <option value="">Any data source</option>
        <option value="sec">SEC filings (10+ yrs)</option>
        <option value="yahoo">Yahoo (~4 yrs)</option>
        <option value="none">No fundamentals</option>
      </select>

      <div className="presets" role="group" aria-label="Column presets">
        {Object.keys(PRESETS).map((name) => (
          <button
            key={name}
            className={preset === name ? 'active' : undefined}
            onClick={() => onPreset(name)}
          >
            {name}
          </button>
        ))}
      </div>

      <button
        className={heat ? 'heat-toggle active' : 'heat-toggle'}
        onClick={() => onHeat(!heat)}
        aria-pressed={heat}
        title={'Shade each cell by where it sits within its own sector. '
          + 'Blue is below the sector median, amber above — a position, not a verdict.'}
      >
        <span className="heat-swatch" aria-hidden />
        Sector shading
      </button>

      <div className="filters-right">
        <span className="count">
          {visibleCount.toLocaleString()} shown
          {dirty && (
            <button className="link" onClick={() => onChange(EMPTY_FILTERS)}>reset</button>
          )}
        </span>
        <button className="export" onClick={onExport}>Export CSV</button>
      </div>
    </div>
  )
}

/** Apply the filter state to the dataset. */
export function applyFilters(rows: Stock[], f: FilterState): Stock[] {
  const q = f.search.trim().toLowerCase()
  return rows.filter((r) => {
    if (q && !r.name.toLowerCase().includes(q) && !r.symbol.toLowerCase().includes(q)) {
      return false
    }
    if (f.country && r.country !== f.country) return false
    if (f.sector && r.sector !== f.sector) return false
    if (f.source && r.fundamentals_source !== f.source) return false
    if (f.minCapB > 0) {
      if (r.market_cap_usd == null) return false
      if (r.market_cap_usd < f.minCapB * 1e9) return false
    }
    return true
  })
}
