import { useState, useRef, useCallback, useEffect } from 'react'
import type { Engine, ArbResult, SportsbookSettings, PredictionSettings, CbbSettings } from './types'

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmt(n: number, decimals = 2) {
  return n.toFixed(decimals)
}

function pct(n: number) {
  return `${fmt(n * 100, 3)}%`
}

// ── Card sub-components ───────────────────────────────────────────────────────

function ExternalLink({ href, label }: { href: string; label: string }) {
  if (!href) return null
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="card-link">
      {label} <span className="card-link-arrow">↗</span>
    </a>
  )
}

function SportsbookCard({ arb }: { arb: ArbResult }) {
  const d = arb.data
  const profitPct = (d.profit_pct as number) ?? 0
  const legs = d.legs as Record<string, { bookmaker: string; odds: number; stake: number; link?: string; fee?: number }> | undefined

  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">{String(d.match_name ?? '—')}</div>
          <div className="card-title-sub">{String(d.league ?? '')} · {String(d.market_key ?? '')} · starts in {fmt(d.hours_to_start as number ?? 0)}h</div>
        </div>
        <span className={`badge ${profitPct > 0 ? 'badge-green' : 'badge-neutral'}`}>
          +{fmt(profitPct, 3)}%
        </span>
      </div>

      {d.state && (
        <div className="card-meta">
          <span className="card-meta-item">After {String(d.state).toUpperCase()} adjustments: <span className="profit-green">{fmt((d.adjusted_profit_pct as number) ?? 0, 3)}%</span></span>
          <span className="card-meta-item meta-dot">·</span>
          <span className="card-meta-item">Fees: ${fmt((d.total_fees as number) ?? 0)}</span>
        </div>
      )}

      {legs && Object.keys(legs).length > 0 && (
        <div className="card-body">
          {Object.entries(legs).map(([outcome, leg]) => (
            <div key={outcome} className="leg">
              <div className="leg-left">
                <span className="leg-platform">{leg.bookmaker}</span>
                <span className="leg-label">{outcome}</span>
              </div>
              <div className="leg-right">
                <span className="leg-price mono">{fmt(leg.odds ?? 0, 2)}x</span>
                <span className="leg-stake mono">${fmt(leg.stake ?? 0)}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {legs && (
        <div className="card-links">
          {Object.values(legs).map((leg, i) =>
            leg.link ? <ExternalLink key={i} href={leg.link} label={`${leg.bookmaker}`} /> : null
          )}
        </div>
      )}
    </div>
  )
}

function CrossExchangeGroup({ group }: { group: ArbResult[] }) {
  const first = group[0].data
  const title = String(first.kalshi_ticker ?? '—')
  const similarity = (first.similarity as number) ?? 0

  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">{String(first.match_name ?? title).split('  vs')[0]}</div>
          <div className="card-title-sub">Kalshi · Polymarket · similarity {fmt(similarity, 2)}</div>
        </div>
        <span className="badge badge-green">
          {group.length} direction{group.length > 1 ? 's' : ''}
        </span>
      </div>

      {group.map((arb, i) => {
        const d = arb.data
        const direction = String(d.direction ?? '')
        const edgePct = (d.edge as number ?? 0) * 100
        const contracts = d.max_contracts as number ?? 0
        const isSell = d.side === 'sell'
        const pYes = isSell ? (d.vwap_yes_bid ?? d.best_bid_yes ?? 0) : (d.vwap_yes ?? d.best_ask_yes ?? 0)
        const pNo  = isSell ? (d.vwap_no_bid  ?? d.best_bid_no  ?? 0) : (d.vwap_no  ?? d.best_ask_no  ?? 0)
        const bigEdge = edgePct > 5

        return (
          <div key={i} className="direction-item">
            <div className="direction-head">
              <span className="direction-label">{direction}</span>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                {bigEdge && <span className="warning-pill">⚠ verify manually</span>}
                <span className="badge badge-green">{fmt(edgePct, 3)}% edge</span>
              </div>
            </div>
            <div className="direction-body">
              {isSell ? (
                <>
                  <div className="leg">
                    <div className="leg-left">
                      <span className="leg-platform">Kalshi</span>
                      <span className="leg-label">YES (sell)</span>
                    </div>
                    <span className="leg-price mono">${fmt(pYes as number, 4)} bid · {fmt(contracts, 2)} contracts</span>
                  </div>
                  <div className="leg">
                    <div className="leg-left">
                      <span className="leg-platform">Polymarket</span>
                      <span className="leg-label">NO (sell)</span>
                    </div>
                    <span className="leg-price mono">${fmt(pNo as number, 4)} bid · {fmt(contracts, 2)} contracts</span>
                  </div>
                </>
              ) : direction.includes('YES@Kalshi') ? (
                <>
                  <div className="leg">
                    <div className="leg-left"><span className="leg-platform">Kalshi</span><span className="leg-label">YES</span></div>
                    <span className="leg-price mono">${fmt((pYes as number) * contracts, 2)} @ ${fmt(pYes as number, 4)}/contract</span>
                  </div>
                  <div className="leg">
                    <div className="leg-left"><span className="leg-platform">Polymarket</span><span className="leg-label">NO</span></div>
                    <span className="leg-price mono">${fmt((pNo as number) * contracts, 2)} @ ${fmt(pNo as number, 4)}/contract</span>
                  </div>
                </>
              ) : (
                <>
                  <div className="leg">
                    <div className="leg-left"><span className="leg-platform">Polymarket</span><span className="leg-label">YES</span></div>
                    <span className="leg-price mono">${fmt((pYes as number) * contracts, 2)} @ ${fmt(pYes as number, 4)}/contract</span>
                  </div>
                  <div className="leg">
                    <div className="leg-left"><span className="leg-platform">Kalshi</span><span className="leg-label">NO</span></div>
                    <span className="leg-price mono">${fmt((pNo as number) * contracts, 2)} @ ${fmt(pNo as number, 4)}/contract</span>
                  </div>
                </>
              )}
            </div>
          </div>
        )
      })}

      <div className="card-links">
        {first.kalshi_link && <ExternalLink href={String(first.kalshi_link)} label="Kalshi" />}
        {first.poly_link   && <ExternalLink href={String(first.poly_link)}   label="Polymarket" />}
      </div>
    </div>
  )
}

function CombinatorialCard({ arb }: { arb: ArbResult }) {
  const d = arb.data
  const roi = (d.roi as number ?? 0) * 100
  const legs = d.legs as { side: string; label?: string; contract_id?: string; qty: number; avg_price: number }[] | undefined

  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">{String(d.match_name ?? '—')}</div>
          <div className="card-title-sub">{String(d.strategy ?? '')} · {String(d.source ?? '')}</div>
        </div>
        <span className="badge badge-green">ROI {fmt(roi, 3)}%</span>
      </div>

      <div className="card-meta">
        <span className="card-meta-item">Guarantee: <span className="profit-green">${fmt(d.max_profit as number ?? 0, 4)}</span></span>
        <span className="meta-dot">·</span>
        <span className="card-meta-item">Cost: ${fmt(d.total_cost as number ?? 0, 4)}</span>
        <span className="meta-dot">·</span>
        <span className="card-meta-item">Worst payout: ${fmt(d.worst_case_payout as number ?? 0, 4)}</span>
      </div>

      {legs && legs.length > 0 && (
        <div className="card-body">
          {legs.map((leg, i) => (
            <div key={i} className="leg">
              <div className="leg-left">
                <span className="leg-platform">{leg.side}</span>
                <span className="leg-label">{leg.label ?? leg.contract_id ?? '—'}</span>
              </div>
              <div className="leg-right">
                <span className="leg-price mono">avg ${fmt(leg.avg_price, 4)}</span>
                <span className="leg-stake mono">{fmt(leg.qty, 2)} contracts</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {d.link && (
        <div className="card-links">
          <ExternalLink href={String(d.link)} label="Polymarket" />
        </div>
      )}
    </div>
  )
}

function PairwiseCard({ arb }: { arb: ArbResult }) {
  const d = arb.data
  const edgePct = (d.edge as number ?? 0) * 100
  const isSell = d.side === 'sell'
  const pYes = isSell ? (d.vwap_yes_bid ?? d.best_bid_yes ?? 0) as number : (d.vwap_yes ?? d.best_ask_yes ?? 0) as number
  const pNo  = isSell ? (d.vwap_no_bid  ?? d.best_bid_no  ?? 0) as number : (d.vwap_no  ?? d.best_ask_no  ?? 0) as number

  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">{String(d.match_name ?? '—')}</div>
          <div className="card-title-sub">{String(d.source ?? '')} · {isSell ? 'sell' : 'buy'}</div>
        </div>
        <span className="badge badge-green">{fmt(edgePct, 3)}% edge</span>
      </div>
      <div className="card-body">
        <div className="leg">
          <div className="leg-left"><span className="leg-platform">YES</span></div>
          <span className="leg-price mono">${fmt(pYes, 4)}</span>
        </div>
        <div className="leg">
          <div className="leg-left"><span className="leg-platform">NO</span></div>
          <span className="leg-price mono">${fmt(pNo, 4)}</span>
        </div>
      </div>
      {d.link && <div className="card-links"><ExternalLink href={String(d.link)} label="Open" /></div>}
    </div>
  )
}

function CbbCard({ arb }: { arb: ArbResult }) {
  const d = arb.data
  const edgePct = (d.edge as number ?? 0) * 100
  const isArb   = edgePct > 0
  const isTotal = String(d.strategy ?? '').includes('total')

  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">{String(d.match_name ?? '—')}</div>
          <div className="card-title-sub">
            {isTotal ? 'O/U' : 'Moneyline'} · Kalshi × Polymarket · {String(d.game_date ?? '')}
          </div>
        </div>
        <span className={`badge ${isArb ? 'badge-green' : 'badge-neutral'}`}>
          {isArb ? '+' : ''}{fmt(edgePct, 3)}%
        </span>
      </div>

      <div className="card-meta">
        <span className="card-meta-item">Sum: <span className={isArb ? 'profit-green' : 'profit-red'}>${fmt(d.sum as number ?? 0, 4)}</span></span>
        <span className="meta-dot">·</span>
        <span className="card-meta-item">Direction: {String(d.direction ?? '')}</span>
      </div>

      <div className="card-body">
        <div className="leg">
          <div className="leg-left">
            <span className="leg-platform">Kalshi</span>
            <span className="leg-label">{String(d.kalshi_leg ?? '')}</span>
          </div>
          <span className="leg-price mono">${fmt(d.kalshi_price as number ?? 0, 4)}</span>
        </div>
        <div className="leg">
          <div className="leg-left">
            <span className="leg-platform">Polymarket</span>
            <span className="leg-label">{String(d.poly_leg ?? '')}</span>
          </div>
          <span className="leg-price mono">${fmt(d.poly_price as number ?? 0, 4)}</span>
        </div>
      </div>

      <div className="card-links">
        {d.kalshi_link && <ExternalLink href={String(d.kalshi_link)} label="Kalshi" />}
        {d.poly_link   && <ExternalLink href={String(d.poly_link)}   label="Polymarket" />}
      </div>
    </div>
  )
}

// ── Results renderer ──────────────────────────────────────────────────────────

function ResultsList({ results }: { results: ArbResult[] }) {
  if (results.length === 0) return null

  // Group cross_exchange by market_id
  const crossGroups: Map<string, ArbResult[]> = new Map()
  const ordered: Array<ArbResult[] | ArbResult> = []

  for (const r of results) {
    if (r.data.strategy === 'cross_exchange') {
      const mid = String(r.data.market_id ?? '')
      if (!crossGroups.has(mid)) {
        crossGroups.set(mid, [])
        ordered.push(crossGroups.get(mid)!)
      }
      crossGroups.get(mid)!.push(r)
    } else {
      ordered.push(r)
    }
  }

  return (
    <div className="results-list">
      {ordered.map((item, i) => {
        if (Array.isArray(item)) {
          return <CrossExchangeGroup key={i} group={item} />
        }
        const strategy = String(item.data.strategy ?? '')
        if (item.engine === 'sportsbook')              return <SportsbookCard    key={i} arb={item} />
        if (item.engine === 'cbb')                     return <CbbCard           key={i} arb={item} />
        if (strategy.startsWith('combinatorial_ip'))   return <CombinatorialCard key={i} arb={item} />
        return <PairwiseCard key={i} arb={item} />
      })}
    </div>
  )
}

// ── Settings panels ───────────────────────────────────────────────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <label className="field-label">{label}</label>
      {children}
    </div>
  )
}

function Input({ value, onChange, type = 'text', step }: { value: string | number; onChange: (v: string) => void; type?: string; step?: string }) {
  return (
    <input
      className="field-input"
      type={type}
      step={step}
      value={value}
      onChange={e => onChange(e.target.value)}
    />
  )
}

function Select({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[] }) {
  return (
    <select className="field-select" value={value} onChange={e => onChange(e.target.value)}>
      {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  )
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="checkbox-label">
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />
      {label}
    </label>
  )
}

function SportsbookSettings({ s, set }: { s: SportsbookSettings; set: (p: Partial<SportsbookSettings>) => void }) {
  return (
    <div className="settings-section">
      <div className="field-row single">
        <Field label="API Key">
          <Input value={s.key} onChange={v => set({ key: v })} type="password" />
        </Field>
      </div>
      <div className="field-row">
        <Field label="Region">
          <Select value={s.region} onChange={v => set({ region: v })} options={[
            { value: 'us', label: 'US' }, { value: 'eu', label: 'EU' },
            { value: 'uk', label: 'UK' }, { value: 'au', label: 'AU' },
          ]} />
        </Field>
        <Field label="State (optional)">
          <Input value={s.state} onChange={v => set({ state: v })} />
        </Field>
      </div>
      <div className="field-row">
        <Field label="Bankroll ($)">
          <Input value={s.bankroll} onChange={v => set({ bankroll: parseFloat(v) || 0 })} type="number" step="10" />
        </Field>
        <Field label="Cutoff %">
          <Input value={s.cutoff} onChange={v => set({ cutoff: parseFloat(v) || 0 })} type="number" step="0.1" />
        </Field>
      </div>
      <div className="field-row">
        <Field label="Market">
          <Input value={s.market} onChange={v => set({ market: v })} />
        </Field>
        <Field label="Bookmakers">
          <Input value={s.bookmakers} onChange={v => set({ bookmakers: v })} />
        </Field>
      </div>
      <div className="checkbox-group">
        <Check label="Use preset US sportsbooks" checked={s.usSportsbooks} onChange={v => set({ usSportsbooks: v })} />
        <Check label="Include started matches"   checked={s.includeStarted} onChange={v => set({ includeStarted: v })} />
      </div>
    </div>
  )
}

function PredictionSettingsPanel({ s, set }: { s: PredictionSettings; set: (p: Partial<PredictionSettings>) => void }) {
  return (
    <div className="settings-section">
      <div className="field-row">
        <Field label="Source">
          <Select value={s.source} onChange={v => set({ source: v })} options={[
            { value: 'all', label: 'All' }, { value: 'kalshi', label: 'Kalshi' },
            { value: 'polymarket', label: 'Polymarket' }, { value: 'cross', label: 'Cross' },
          ]} />
        </Field>
        <Field label="Strategy">
          <Select value={s.strategy} onChange={v => set({ strategy: v })} options={[
            { value: 'combinatorial', label: 'Combinatorial' }, { value: 'pairwise', label: 'Pairwise' },
          ]} />
        </Field>
      </div>
      <div className="field-row">
        <Field label="Market Limit">
          <Input value={s.limit} onChange={v => set({ limit: parseInt(v) || 0 })} type="number" step="100" />
        </Field>
        <Field label="Cross Similarity">
          <Input value={s.crossSimilarity} onChange={v => set({ crossSimilarity: parseFloat(v) || 0 })} type="number" step="0.05" />
        </Field>
      </div>
      <div className="field-row">
        <Field label="Min Edge %">
          <Input value={s.minEdge} onChange={v => set({ minEdge: parseFloat(v) || 0 })} type="number" step="0.1" />
        </Field>
        <Field label="Min Profit/Contract">
          <Input value={s.minProfitPerContract} onChange={v => set({ minProfitPerContract: parseFloat(v) || 0 })} type="number" step="0.01" />
        </Field>
      </div>
      <div className="field-row">
        <Field label="Fee (bps)">
          <Input value={s.feeBps} onChange={v => set({ feeBps: parseFloat(v) || 0 })} type="number" step="1" />
        </Field>
        <Field label="Slippage (bps)">
          <Input value={s.slippageBps} onChange={v => set({ slippageBps: parseFloat(v) || 0 })} type="number" step="1" />
        </Field>
      </div>
      <div className="checkbox-group">
        <Check label="Assume exhaustive bundle"       checked={s.assumeExhaustive}          onChange={v => set({ assumeExhaustive: v })} />
        <Check label="Strict bundle completeness"     checked={s.strictBundleCompleteness}  onChange={v => set({ strictBundleCompleteness: v })} />
      </div>
    </div>
  )
}

function CbbSettingsPanel({ s, set }: { s: CbbSettings; set: (p: Partial<CbbSettings>) => void }) {
  return (
    <div className="settings-section">
      <div className="field-row">
        <Field label="Min Edge %">
          <Input value={s.minEdge} onChange={v => set({ minEdge: parseFloat(v) || 0 })} type="number" step="0.1" />
        </Field>
        <Field label="Arb Threshold">
          <Input value={s.threshold} onChange={v => set({ threshold: parseFloat(v) || 1 })} type="number" step="0.01" />
        </Field>
      </div>
      <div className="checkbox-group">
        <Check label="Include Over/Under totals" checked={s.includeTotals} onChange={v => set({ includeTotals: v })} />
      </div>
    </div>
  )
}

// ── Main App ──────────────────────────────────────────────────────────────────

export default function App() {
  const [engine, setEngine]     = useState<Engine>('prediction')
  const [timeout, setTimeout_]  = useState(30)
  const [isRunning, setRunning] = useState(false)
  const [status, setStatus]     = useState('')
  const [error, setError]       = useState('')
  const [results, setResults]   = useState<ArbResult[]>([])
  const esRef = useRef<EventSource | null>(null)

  const [sb, setSb] = useState<SportsbookSettings>({
    key: '', region: 'us', state: '', market: 'h2h',
    cutoff: 0, bankroll: 100, includeStarted: false,
    bookmakers: '', usSportsbooks: false,
  })

  const [pred, setPred] = useState<PredictionSettings>({
    source: 'cross', strategy: 'combinatorial', limit: 500,
    crossSimilarity: 0.5, minEdge: 0, minProfitPerContract: 0.01,
    levelsPerContract: 5, assumeExhaustive: true,
    strictBundleCompleteness: true, feeBps: 5, slippageBps: 10,
  })

  const [cbb, setCbb] = useState<CbbSettings>({
    minEdge: 0, threshold: 1.0, includeTotals: true,
  })

  const countRef = useRef(0)

  const handleRun = useCallback(() => {
    if (isRunning) {
      esRef.current?.close()
      setRunning(false)
      setStatus(`Stopped — ${countRef.current} results`)
      return
    }

    setResults([])
    setError('')
    setRunning(true)
    setStatus('Starting...')
    countRef.current = 0

    const p = new URLSearchParams()
    p.set('engine',  engine)
    p.set('timeout', String(timeout))

    if (engine === 'sportsbook') {
      if (sb.key) p.set('key', sb.key)
      p.set('region',           sb.region)
      p.set('state',            sb.state)
      p.set('market',           sb.market)
      p.set('cutoff',           String(sb.cutoff))
      p.set('bankroll',         String(sb.bankroll))
      p.set('include_started',  String(sb.includeStarted))
      p.set('bookmakers',       sb.bookmakers)
      p.set('us_sportsbooks',   String(sb.usSportsbooks))
    } else if (engine === 'prediction') {
      p.set('prediction_source',                    pred.source)
      p.set('prediction_strategy',                  pred.strategy)
      p.set('prediction_limit',                     String(pred.limit))
      p.set('prediction_cross_similarity',          String(pred.crossSimilarity))
      p.set('prediction_min_edge',                  String(pred.minEdge))
      p.set('prediction_min_profit_per_contract',   String(pred.minProfitPerContract))
      p.set('prediction_levels_per_contract',       String(pred.levelsPerContract))
      p.set('prediction_assume_exhaustive',         String(pred.assumeExhaustive))
      p.set('prediction_strict_bundle_completeness', String(pred.strictBundleCompleteness))
      p.set('prediction_fee_bps',                   String(pred.feeBps))
      p.set('prediction_slippage_bps',              String(pred.slippageBps))
    } else if (engine === 'cbb') {
      p.set('cbb_min_edge',       String(cbb.minEdge))
      p.set('cbb_threshold',      String(cbb.threshold))
      p.set('cbb_include_totals', String(cbb.includeTotals))
    }

    const es = new EventSource(`/api/stream?${p.toString()}`)
    esRef.current = es

    es.onmessage = (event: MessageEvent) => {
      const msg = JSON.parse(event.data as string)
      if (msg.type === 'arb') {
        countRef.current += 1
        setResults(prev => [...prev, msg as ArbResult])
        setStatus(`${countRef.current} found`)
      } else if (msg.type === 'status') {
        setStatus(String(msg.message))
      } else if (msg.type === 'error') {
        setError(String(msg.message))
        setRunning(false)
        es.close()
      } else if (msg.type === 'done') {
        setRunning(false)
        setStatus(`Complete — ${countRef.current} result${countRef.current !== 1 ? 's' : ''}`)
        es.close()
      }
    }

    es.onerror = () => {
      setError('Connection to server failed. Is the server running?')
      setRunning(false)
      es.close()
    }
  }, [engine, timeout, sb, pred, cbb, isRunning])

  // Clean up on unmount
  useEffect(() => () => { esRef.current?.close() }, [])

  const engineLabel = engine === 'sportsbook' ? 'Sportsbook' : engine === 'cbb' ? 'CBB' : 'Prediction'
  const hasResults = results.length > 0

  return (
    <div className="app">
      <header className="app-header">
        <span className="app-title">Arbitrage <em>Finder</em></span>
        <span style={{ color: 'var(--text-faint)', fontSize: 12 }}>·</span>
        <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>{engineLabel}</span>
      </header>

      <div className="app-body">
        {/* ── Sidebar ── */}
        <aside className="sidebar">
          <div className="engine-tabs">
            {(['sportsbook', 'prediction', 'cbb'] as Engine[]).map(e => (
              <button
                key={e}
                className={`engine-tab${engine === e ? ' active' : ''}`}
                onClick={() => { if (!isRunning) setEngine(e) }}
                disabled={isRunning}
              >
                {e === 'sportsbook' ? 'Sportsbook' : e === 'cbb' ? 'CBB' : 'Prediction'}
              </button>
            ))}
          </div>

          {engine === 'sportsbook' && (
            <SportsbookSettings s={sb} set={p => setSb(prev => ({ ...prev, ...p }))} />
          )}
          {engine === 'prediction' && (
            <PredictionSettingsPanel s={pred} set={p => setPred(prev => ({ ...prev, ...p }))} />
          )}
          {engine === 'cbb' && (
            <CbbSettingsPanel s={cbb} set={p => setCbb(prev => ({ ...prev, ...p }))} />
          )}

          <div className="sidebar-footer">
            <div className="timeout-row">
              <label className="field-label" style={{ minWidth: 60 }}>Timeout (s)</label>
              <input
                className="field-input"
                type="number"
                step="5"
                value={timeout}
                onChange={e => setTimeout_(parseInt(e.target.value) || 10)}
                style={{ width: 70 }}
              />
            </div>
            <button className={`run-btn${isRunning ? ' running' : ''}`} onClick={handleRun}>
              {isRunning ? 'Stop' : 'Run Search'}
            </button>
          </div>
        </aside>

        {/* ── Main content ── */}
        <main className="main-content">
          <div className="status-bar">
            <div className={`status-dot${isRunning ? ' running' : hasResults ? ' done' : ''}`} />
            <span>{status || 'Ready to search'}</span>
            {hasResults && (
              <span className="status-count">{results.length} result{results.length !== 1 ? 's' : ''}</span>
            )}
          </div>

          {error && <div className="error-banner">{error}</div>}

          <div className="results-scroll">
            {!hasResults && !isRunning && !error && (
              <div className="empty-state">
                <div className="empty-title">No results yet</div>
                <div className="empty-sub">Configure your settings and press Run Search to find arbitrage opportunities.</div>
              </div>
            )}
            <ResultsList results={results} />
          </div>
        </main>
      </div>
    </div>
  )
}
