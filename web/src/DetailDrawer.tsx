import { useEffect } from 'react'
import type { Stock } from './types'
import { EMPTY, money, percent, plain, ratio, sourceLabel } from './format'

interface Props {
  stock: Stock | null
  spark: number[] | undefined
  onClose: () => void
}

/** Inline SVG sparkline — no chart library needed for 60 points. */
function Sparkline({ points }: { points: number[] }) {
  if (points.length < 2) {
    return <p className="muted">No price history available.</p>
  }
  const W = 560
  const H = 130
  const PAD = 4
  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = max - min || 1
  const step = (W - PAD * 2) / (points.length - 1)

  const path = points
    .map((v, i) => {
      const x = PAD + i * step
      const y = PAD + (H - PAD * 2) * (1 - (v - min) / span)
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  const rising = points[points.length - 1] >= points[0]
  const stroke = rising ? 'var(--pos)' : 'var(--neg)'

  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} role="img"
         aria-label={`Price history, ${rising ? 'up' : 'down'} overall`}
         preserveAspectRatio="none">
      <path d={`${path} L${W - PAD},${H} L${PAD},${H} Z`} fill={stroke} opacity="0.10" />
      <path d={path} fill="none" stroke={stroke} strokeWidth="2"
            strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

function Row({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="detail-row">
      <dt title={hint}>{label}</dt>
      <dd className={value === EMPTY ? 'empty' : undefined}>{value}</dd>
    </div>
  )
}

export function DetailDrawer({ stock, spark, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!stock) return null

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={`${stock.name} details`}>
        <header>
          <div>
            <h2>{stock.name}</h2>
            <p className="muted">
              {stock.symbol} · {plain(stock.exchange)} · {plain(stock.country)}
            </p>
          </div>
          <button className="close" onClick={onClose} aria-label="Close">×</button>
        </header>

        <Sparkline points={spark ?? []} />
        {stock.history_start && (
          <p className="muted spark-caption">
            Price history from {stock.history_start}
          </p>
        )}

        <dl>
          <Row label="Market cap" value={money(stock.market_cap_usd)} />
          <Row label="Sector" value={plain(stock.sector)} />
          <Row label="Industry" value={plain(stock.industry)} />
          <Row label="P/E (trailing)" value={ratio(stock.trailing_pe)} />
          <Row label="P/E (forward)" value={ratio(stock.forward_pe)} />
          <Row label="Dividend yield" value={percent(stock.dividend_yield, 2)} />

          <h3>Annualised total return</h3>
          <Row label="1 year" value={percent(stock.return_1y)} />
          <Row label="5 years" value={percent(stock.return_5y)} />
          <Row label="10 years" value={percent(stock.return_10y)} />
          <Row label="20 years" value={percent(stock.return_20y)} />

          <h3>Business growth</h3>
          <Row label="Revenue 3Y" value={percent(stock.revenue_cagr_3y)} />
          <Row label="Revenue 5Y" value={percent(stock.revenue_cagr_5y)} />
          <Row label="Revenue 10Y" value={percent(stock.revenue_cagr_10y)} />
          <Row label="Net income 5Y" value={percent(stock.net_income_cagr_5y)}
               hint="Net income rather than EPS, so share splits do not distort it." />
          <Row label="Net income 10Y" value={percent(stock.net_income_cagr_10y)} />

          <h3>Quality and risk</h3>
          <Row label="Profit margin" value={percent(stock.profit_margin)} />
          <Row label="Return on equity" value={percent(stock.return_on_equity)} />
          <Row label="Beta" value={ratio(stock.beta, 2)} />
          <Row label="Max drawdown" value={percent(stock.max_drawdown)} />
        </dl>

        <footer>
          <p className="muted">
            Fundamentals: <strong>{sourceLabel(stock.fundamentals_source)}</strong>
            {stock.fundamentals_years > 0 && ` · ${stock.fundamentals_years} years`}
          </p>
          {stock.stale && (
            <p className="warn">
              Some values were carried over from the previous run because the latest
              refresh could not retrieve them.
            </p>
          )}
        </footer>
      </aside>
    </>
  )
}
