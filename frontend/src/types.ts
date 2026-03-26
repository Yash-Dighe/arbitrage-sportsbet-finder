export type Engine = 'sportsbook' | 'prediction' | 'cbb'

export interface SportsbookSettings {
  key: string
  region: string
  state: string
  market: string
  cutoff: number
  bankroll: number
  includeStarted: boolean
  bookmakers: string
  usSportsbooks: boolean
}

export interface PredictionSettings {
  source: string
  strategy: string
  limit: number
  crossSimilarity: number
  minEdge: number
  minProfitPerContract: number
  levelsPerContract: number
  assumeExhaustive: boolean
  strictBundleCompleteness: boolean
  feeBps: number
  slippageBps: number
}

export interface CbbSettings {
  minEdge: number
  threshold: number
  includeTotals: boolean
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type ArbData = Record<string, any>

export interface ArbResult {
  type: 'arb'
  engine: Engine
  data: ArbData
}

export interface StatusMsg { type: 'status'; message: string }
export interface ErrorMsg  { type: 'error';  message: string }
export interface DoneMsg   { type: 'done' }

export type SSEMessage = ArbResult | StatusMsg | ErrorMsg | DoneMsg
