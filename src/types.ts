export type RangePreset = "1W" | "MTD" | "1M" | "3M" | "YTD" | "1Y" | "ALL" | "CUSTOM";
export type ReturnMethod = "SIMPLE" | "TWR" | "MWR";

export interface Meta {
  accounts: string[];
  firstDate: string;
  lastDate: string;
  lastSync: string | null;
  reports: number;
  records: number;
  database: string;
}

export interface SeriesPoint {
  date: string;
  nlv: number;
  flow: number;
  simple: number;
  twr: number;
  mwr: number | null;
  spy?: number;
  qqq?: number;
}

export interface Holding {
  account: string;
  symbol: string;
  underlying: string;
  assetClass: string;
  currency: string;
  quantity: number;
  markPrice: number;
  marketValue: number;
  costBasis: number;
  unrealized: number;
  unrealizedPercent: number;
}

export interface ClosedTrade {
  symbol: string;
  realized: number;
  closedLots: number;
  firstClose: string | null;
  lastClose: string | null;
}

export interface MoneyDay {
  date: string;
  deposits: number;
  withdrawals: number;
  securityTransfers: number;
  dividends: number;
  interestIncome: number;
  income: number;
  commissions: number;
  financing: number;
  fees: number;
  taxes: number;
}

export interface ClosedPnlDay {
  date: string;
  symbol: string;
  realized: number;
}

export interface Dashboard {
  range: { preset: RangePreset; from: string; to: string };
  account: string;
  algorithm: ReturnMethod;
  metrics: {
    nlv: number;
    nlvChange: number;
    startingNlv: number;
    returnPercent: number | null;
    annualizedReturnPercent: number | null;
    realized: number;
    unrealized: number;
    unrealizedChange: number;
    totalPnl: number;
    netContributions: number;
    cashNetContributions: number;
    securityNetTransfers: number;
    income: number;
    fees: number;
    financing: number;
    taxes: number;
  };
  returns: { simple: number | null; twr: number | null; mwr: number | null };
  benchmarkReturns: { spy: number | null; qqq: number | null };
  pnlComponents: { label: string; value: number }[];
  series: SeriesPoint[];
  holdingsDate: string | null;
  holdings: Holding[];
  closedTrades: ClosedTrade[];
  closedTradeContracts: ClosedTrade[];
  closedPnlDaily: ClosedPnlDay[];
  mtm: {
    coverageFrom: string | null;
    coverageTo: string | null;
    days: number;
    priorOpen: number;
    transactions: number;
    commissions: number;
    otherWithAccruals: number;
    reportedTotal: number;
    componentTotal: number;
    difference: number;
  };
  money: {
    dividendsBySymbol: { date: string; symbol: string; amount: number }[];
    totals: Record<string, number>;
    daily: MoneyDay[];
  };
}
