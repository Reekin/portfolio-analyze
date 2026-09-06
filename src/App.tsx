import { startTransition, useEffect, useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@wealthfolio/ui";
import {
  ArrowDownLeft,
  ArrowUpRight,
  Bank,
  CalendarBlank,
  ChartLineUp,
  Coins,
  Database,
  Gauge,
  HandCoins,
  Receipt,
  ShieldCheck,
  TrendDown,
  TrendUp,
  Wallet,
} from "@phosphor-icons/react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchDashboard, fetchMeta } from "./api";
import type { Dashboard, Meta, RangePreset, ReturnMethod } from "./types";

const ranges: RangePreset[] = ["1W", "MTD", "1M", "3M", "YTD", "1Y", "ALL", "CUSTOM"];
const methods: { value: ReturnMethod; label: string }[] = [
  { value: "SIMPLE", label: "简单收益" },
  { value: "TWR", label: "时间加权" },
  { value: "MWR", label: "资金加权" },
];
type FlowMode = "flows" | "dividends" | "fees" | "closed";
type ClosedGrouping = "contract" | "underlying";
type PerformanceChartMode = "performance" | "nlv";
type FlowChartPoint = {
  date: string;
  deposits?: number;
  withdrawals?: number;
  securityTransfers?: number;
  value?: number;
  realized?: number;
};

const flowModes: { value: FlowMode; label: string }[] = [
  { value: "flows", label: "资金进出" },
  { value: "dividends", label: "分红" },
  { value: "fees", label: "交易费用" },
  { value: "closed", label: "已关闭损益" },
];

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});
const moneyPrecise = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const compactMoney = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});
const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

function signedMoney(value: number) {
  const normalized = Math.abs(value) < 0.005 ? 0 : value;
  return `${normalized >= 0 ? "+" : ""}${moneyPrecise.format(normalized)}`;
}

function MetricCard({
  label,
  value,
  detail,
  secondary,
  tone = "neutral",
  icon,
}: {
  label: string;
  value: string;
  detail: string;
  secondary?: string;
  tone?: "positive" | "negative" | "neutral";
  icon: React.ReactNode;
}) {
  return (
    <Card className="metric-card">
      <CardContent className="metric-inner">
        <div className={`metric-icon ${tone}`}>{icon}</div>
        <div>
          <p className="eyebrow">{label}</p>
          <p className={`metric-value ${tone}`}>{value}</p>
          {secondary && <p className="metric-annualized">{secondary}</p>}
          <p className="metric-detail">{detail}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function ChartTooltip({
  active,
  payload,
  label,
  percent = false,
  performance = false,
}: {
  active?: boolean;
  payload?: Array<{ value: number; name: string; color: string }>;
  label?: string;
  percent?: boolean;
  performance?: boolean;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <strong>{label}</strong>
      {payload.map((item) => (
        <span key={item.name}>
          <i style={{ background: item.color }} />
          {item.name}: {
            percent || (performance && item.name !== "净值")
              ? `${number.format(Math.abs(item.value) < 0.005 ? 0 : item.value)}%`
              : moneyPrecise.format(Math.abs(item.value) < 0.005 ? 0 : item.value)
          }
        </span>
      ))}
    </div>
  );
}

function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [range, setRange] = useState<RangePreset>("1Y");
  const [method, setMethod] = useState<ReturnMethod>("TWR");
  const [account, setAccount] = useState("ALL");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [appliedCustomFrom, setAppliedCustomFrom] = useState("");
  const [appliedCustomTo, setAppliedCustomTo] = useState("");
  const [customApplyVersion, setCustomApplyVersion] = useState(0);
  const [flowMode, setFlowMode] = useState<FlowMode>("flows");
  const [closedGrouping, setClosedGrouping] = useState<ClosedGrouping>("contract");
  const [closedSymbol, setClosedSymbol] = useState("ALL");
  const [dividendSymbol, setDividendSymbol] = useState("ALL");
  const [performanceChartMode, setPerformanceChartMode] =
    useState<PerformanceChartMode>("performance");
  const [visibleBenchmarks, setVisibleBenchmarks] = useState({ spy: true, qqq: true });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchMeta().then(setMeta).catch((reason) => setError(String(reason)));
  }, []);

  useEffect(() => {
    if (range === "CUSTOM" && (!appliedCustomFrom || !appliedCustomTo)) return;
    let cancelled = false;
    setLoading(true);
    fetchDashboard({
      range,
      method,
      account,
      from: range === "CUSTOM" ? appliedCustomFrom : undefined,
      to: range === "CUSTOM" ? appliedCustomTo : undefined,
    })
      .then((value) => {
        if (!cancelled) {
          startTransition(() => setDashboard(value));
          setError("");
        }
      })
      .catch((reason) => !cancelled && setError(String(reason)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [range, method, account, appliedCustomFrom, appliedCustomTo, customApplyVersion]);

  const effectiveCustomTo = customTo || meta?.lastDate || "";
  const customDatesValid = Boolean(customFrom && effectiveCustomTo && customFrom <= effectiveCustomTo);

  function applyCustomDates() {
    if (!customDatesValid) return;
    setAppliedCustomFrom(customFrom);
    setAppliedCustomTo(effectiveCustomTo);
    setCustomApplyVersion((version) => version + 1);
  }

  function toggleBenchmark(symbol: "spy" | "qqq") {
    setVisibleBenchmarks((current) => ({ ...current, [symbol]: !current[symbol] }));
  }

  const metric = dashboard?.metrics;
  const mtmComplete = Boolean(
    dashboard?.mtm.coverageFrom
      && dashboard?.mtm.coverageTo
      && dashboard.series.length > 0
      && dashboard.mtm.coverageFrom <= dashboard.series[0].date
      && dashboard.mtm.coverageTo >= dashboard.series[dashboard.series.length - 1].date,
  );
  const seriesKey = method.toLowerCase() as "simple" | "twr" | "mwr";
  const closedSource =
    closedGrouping === "underlying"
      ? dashboard?.closedTrades ?? []
      : dashboard?.closedTradeContracts ?? [];
  const topClosed = closedSource.slice(0, 10);
  const maxClosed = Math.max(...topClosed.map((item) => Math.abs(item.realized)), 1);
  const closedSymbols = [...(dashboard?.closedTrades ?? [])]
    .sort((a, b) => b.realized - a.realized || a.symbol.localeCompare(b.symbol))
    .map((item) => item.symbol);
  const selectedClosedSymbol = closedSymbols.includes(closedSymbol)
    ? closedSymbol
    : "ALL";
  const dividendsBySymbol = new Map<string, number>();
  for (const item of dashboard?.money.dividendsBySymbol ?? []) {
    dividendsBySymbol.set(item.symbol, (dividendsBySymbol.get(item.symbol) ?? 0) + item.amount);
  }
  const dividendSymbols = [...dividendsBySymbol]
    .sort(([a, amountA], [b, amountB]) => amountB - amountA || a.localeCompare(b))
    .map(([symbol]) => symbol);
  const selectedDividendSymbol = dividendSymbols.includes(dividendSymbol) ? dividendSymbol : "ALL";
  const selectedFlowSymbol = flowMode === "closed" ? selectedClosedSymbol : selectedDividendSymbol;
  const selectedFlowLabel = selectedFlowSymbol === "ALL" ? "全部标的" : selectedFlowSymbol;
  const groupedFlow = new Map<string, number>();
  if (flowMode === "closed") {
    for (const item of dashboard?.closedPnlDaily ?? []) {
      if (selectedClosedSymbol === "ALL" || item.symbol === selectedClosedSymbol) {
        groupedFlow.set(item.date, (groupedFlow.get(item.date) ?? 0) + item.realized);
      }
    }
  } else if (flowMode === "dividends") {
    for (const item of dashboard?.money.dividendsBySymbol ?? []) {
      if (selectedDividendSymbol === "ALL" || item.symbol === selectedDividendSymbol) {
        groupedFlow.set(item.date, (groupedFlow.get(item.date) ?? 0) + item.amount);
      }
    }
  }
  const flowChartData: FlowChartPoint[] =
    flowMode === "closed" || flowMode === "dividends"
      ? [...groupedFlow].sort(([a], [b]) => a.localeCompare(b)).map(([date, value]) =>
          flowMode === "closed" ? { date, realized: value } : { date, value })
      : dashboard?.money.daily.map((item) => {
          if (flowMode === "flows") {
            return {
              date: item.date,
              deposits: item.deposits,
              withdrawals: item.withdrawals === 0 ? 0 : -item.withdrawals,
              securityTransfers: item.securityTransfers,
            };
          }
          return { date: item.date, value: -item.commissions };
        }) ?? [];
  const dividendTotal = flowChartData.reduce((sum, item) => sum + (item.value ?? 0), 0);
  const closedPnlTotal = flowChartData.reduce(
    (sum, item) => sum + ("realized" in item ? item.realized ?? 0 : 0),
    0,
  );
  const flowTotals = dashboard
    ? flowMode === "flows"
      ? [
          { label: "累计入金", value: dashboard.money.totals.deposits || 0 },
          { label: "累计出金", value: -(dashboard.money.totals.withdrawals || 0) },
          { label: "证券净转移", value: dashboard.metrics.securityNetTransfers },
          { label: "净外部流入", value: dashboard.metrics.netContributions },
        ]
      : flowMode === "dividends"
        ? [{ label: `${selectedFlowLabel} 累计分红`, value: dividendTotal }]
        : flowMode === "fees"
          ? [{ label: "累计交易费用", value: -(dashboard.money.totals.commissions || 0) }]
          : [{ label: `${selectedFlowLabel} 累计 FIFO 损益`, value: closedPnlTotal }]
    : [];
  const costData = dashboard
    ? [
        { name: "佣金", value: dashboard.money.totals.commissions || 0, color: "var(--ink)" },
        { name: "融资利息", value: dashboard.money.totals.interestPaid || 0, color: "var(--rust)" },
        { name: "税费", value: dashboard.money.totals.taxes || 0, color: "var(--ochre)" },
        { name: "其他", value: dashboard.money.totals.otherFees || 0, color: "var(--sage)" },
      ].filter((item) => item.value > 0)
    : [];
  const totalCosts = costData.reduce((sum, item) => sum + item.value, 0);
  const nlvValues = dashboard?.series.map((item) => item.nlv) ?? [];
  const nlvMin = nlvValues.length ? Math.min(...nlvValues) : 0;
  const nlvMax = nlvValues.length ? Math.max(...nlvValues) : 1;
  const nlvPadding = Math.max((nlvMax - nlvMin) * 0.1, nlvMax * 0.015);
  const nlvDomain: [number, number] =
    nlvValues.length > 0
      ? [Math.max(0, nlvMin - nlvPadding), nlvMax + nlvPadding]
      : [0, 1];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-mark">PA</div>
        <nav>
          <button className="nav-item active" aria-label="总览">
            <Gauge size={21} />
          </button>
          <button className="nav-item" aria-label="持仓">
            <Wallet size={21} />
          </button>
          <button className="nav-item" aria-label="交易">
            <ChartLineUp size={21} />
          </button>
          <button className="nav-item" aria-label="现金流">
            <HandCoins size={21} />
          </button>
        </nav>
        <div className="sidebar-foot">
          <ShieldCheck size={20} />
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <p className="kicker">PORTFOLIO ANALYZE</p>
            <h1>资产监控台</h1>
            <p className="subtitle">
              {dashboard ? `${dashboard.range.from} — ${dashboard.range.to}` : "正在连接本地IBKR数据"}
            </p>
          </div>
          <div className="header-actions">
            <div className="sync-pill">
              <span className="sync-dot" />
              {meta?.lastSync ? `已同步 ${new Date(meta.lastSync).toLocaleString("zh-CN")}` : "等待同步"}
            </div>
            <Select value={account} onValueChange={setAccount}>
              <SelectTrigger className="account-select">
                <SelectValue placeholder="全部账户" />
              </SelectTrigger>
              <SelectContent className="app-select-content" align="end" sideOffset={6}>
                <SelectItem value="ALL">全部账户</SelectItem>
                {meta?.accounts.map((item) => (
                  <SelectItem value={item} key={item}>
                    {item}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </header>

        <section className="control-rail">
          <div className="range-group">
            {ranges.map((item) => (
              <Button
                key={item}
                size="sm"
                variant={range === item ? "default" : "ghost"}
                onClick={() => setRange(item)}
                className={`range-button ${range === item ? "selected" : ""}`}
              >
                {item}
              </Button>
            ))}
          </div>
          {range === "CUSTOM" && (
            <div className="custom-dates">
              <input
                type="date"
                value={customFrom}
                min={meta?.firstDate}
                max={customTo || meta?.lastDate}
                onChange={(event) => setCustomFrom(event.target.value)}
              />
              <span>至</span>
              <input
                type="date"
                value={customTo}
                min={customFrom || meta?.firstDate}
                max={meta?.lastDate}
                onChange={(event) => setCustomTo(event.target.value)}
              />
              <Button
                size="sm"
                onClick={applyCustomDates}
                disabled={!customDatesValid}
                className="custom-date-apply"
              >
                {loading && range === "CUSTOM" ? "应用中" : "应用"}
              </Button>
              {!customFrom && (
                <span className="custom-date-hint">
                  请选择开始日期
                </span>
              )}
              {customFrom && !customTo && customDatesValid && (
                <span className="custom-date-hint">
                  结束日期取最新 {meta?.lastDate}
                </span>
              )}
              {customFrom && effectiveCustomTo && !customDatesValid && (
                <span className="custom-date-hint error">结束日期不能早于开始日期</span>
              )}
            </div>
          )}
          <div className="method-group">
            {methods.map((item) => (
              <Button
                key={item.value}
                size="sm"
                variant={method === item.value ? "secondary" : "ghost"}
                onClick={() => setMethod(item.value)}
                className={`method-button ${method === item.value ? "selected" : ""}`}
              >
                {item.label}
              </Button>
            ))}
          </div>
        </section>

        {error && <div className="error-banner">{error}</div>}

        {loading && !dashboard ? (
          <div className="loading-grid">
            {Array.from({ length: 8 }).map((_, index) => (
              <Skeleton className="h-32 rounded-2xl" key={index} />
            ))}
          </div>
        ) : (
          dashboard && (
            <>
              <section className="metrics-grid">
                <MetricCard
                  label="日结账户净值"
                  value={money.format(metric!.nlv)}
                  detail={`截至 ${dashboard.range.to} · ${signedMoney(metric!.nlvChange)} 区间变化`}
                  tone={metric!.nlvChange >= 0 ? "positive" : "negative"}
                  icon={<Bank size={20} />}
                />
                <MetricCard
                  label={`${method} 收益`}
                  value={
                    metric!.returnPercent == null ? "—" : `${metric!.returnPercent >= 0 ? "+" : ""}${number.format(metric!.returnPercent)}%`
                  }
                  secondary={`折算年化 ${metric!.annualizedReturnPercent == null ? "—" : `${metric!.annualizedReturnPercent >= 0 ? "+" : ""}${number.format(metric!.annualizedReturnPercent)}%`}`}
                  detail={method === "SIMPLE" ? "净值变化，含资金进出影响" : "已调整外部资金流"}
                  tone={(metric!.returnPercent ?? 0) >= 0 ? "positive" : "negative"}
                  icon={<ChartLineUp size={20} />}
                />
                <MetricCard
                  label="区间总损益"
                  value={mtmComplete ? signedMoney(metric!.totalPnl) : "—"}
                  detail={
                    mtmComplete
                      ? "直接汇总 MTM、收入及成本"
                      : `MTM 数据覆盖至 ${dashboard.mtm.coverageTo ?? "—"}`
                  }
                  tone={!mtmComplete ? "neutral" : metric!.totalPnl >= 0 ? "positive" : "negative"}
                  icon={metric!.totalPnl >= 0 ? <TrendUp size={20} /> : <TrendDown size={20} />}
                />
                <MetricCard
                  label="已实现盈亏"
                  value={signedMoney(metric!.realized)}
                  detail="FIFO Closed Lots"
                  tone={metric!.realized >= 0 ? "positive" : "negative"}
                  icon={metric!.realized >= 0 ? <TrendUp size={20} /> : <TrendDown size={20} />}
                />
                <MetricCard
                  label="未实现盈亏（期末）"
                  value={signedMoney(metric!.unrealized)}
                  detail={`持仓日期 ${dashboard.holdingsDate ?? "—"}`}
                  tone={metric!.unrealized >= 0 ? "positive" : "negative"}
                  icon={<Coins size={20} />}
                />
                <MetricCard
                  label="净外部流入"
                  value={signedMoney(metric!.netContributions)}
                  detail={`现金 ${signedMoney(metric!.cashNetContributions)} · 证券 ${signedMoney(metric!.securityNetTransfers)}`}
                  tone="neutral"
                  icon={<ArrowDownLeft size={20} />}
                />
                <MetricCard
                  label="收入"
                  value={moneyPrecise.format(metric!.income)}
                  detail="分红及利息收入"
                  tone="positive"
                  icon={<HandCoins size={20} />}
                />
                <MetricCard
                  label="交易费用"
                  value={moneyPrecise.format(metric!.fees)}
                  detail="已计入 FIFO 成本，仅作费用统计"
                  tone="negative"
                  icon={<Receipt size={20} />}
                />
                <MetricCard
                  label="融资与税费"
                  value={moneyPrecise.format(metric!.financing + metric!.taxes)}
                  detail="融资利息、预扣税及交易税"
                  tone="negative"
                  icon={<ShieldCheck size={20} />}
                />
              </section>

              {mtmComplete && (
                <section className="direct-pnl-panel" aria-label="区间总损益构成">
                  <div className="direct-pnl-heading">
                    <div>
                      <strong>区间总损益构成</strong>
                      <span>含应计利息及股息变动，分项分别四舍五入至分</span>
                    </div>
                  </div>
                  <div className="direct-pnl-equation">
                    {dashboard.pnlComponents.map((item, index) => (
                      <div key={item.label}><i>{index ? "+" : ""}</i><span>{item.label}</span><strong>{signedMoney(item.value)}</strong></div>
                    ))}
                    <div className="result"><i>=</i><span>区间总损益</span><strong>{signedMoney(metric!.totalPnl)}</strong></div>
                  </div>
                </section>
              )}

              <section className="dashboard-grid">
                <Card className="performance-card">
                  <CardHeader className="chart-header">
                    <div>
                      <CardTitle>账户净值与业绩</CardTitle>
                      <CardDescription>IBKR Statement Mark 日结口径，不代表可成交平仓价值</CardDescription>
                    </div>
                    <div className="performance-actions">
                      <Badge variant="outline">{method}</Badge>
                      <div className="compact-toggle" aria-label="图表模式">
                        <button
                          className={performanceChartMode === "performance" ? "active" : ""}
                          onClick={() => setPerformanceChartMode("performance")}
                        >
                          业绩对比
                        </button>
                        <button
                          className={performanceChartMode === "nlv" ? "active" : ""}
                          onClick={() => setPerformanceChartMode("nlv")}
                        >
                          净值走势
                        </button>
                      </div>
                      {performanceChartMode === "performance" && (
                        <div className="compact-toggle benchmark-toggle" aria-label="基准指数">
                          <button
                            className={visibleBenchmarks.spy ? "active" : ""}
                            aria-pressed={visibleBenchmarks.spy}
                            onClick={() => toggleBenchmark("spy")}
                          >
                            SPY
                          </button>
                          <button
                            className={visibleBenchmarks.qqq ? "active" : ""}
                            aria-pressed={visibleBenchmarks.qqq}
                            onClick={() => toggleBenchmark("qqq")}
                          >
                            QQQ
                          </button>
                        </div>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="hero-value">
                      <span>{money.format(metric!.nlv)}</span>
                      <strong className={(metric!.returnPercent ?? 0) >= 0 ? "positive" : "negative"}>
                        {metric!.returnPercent == null
                          ? "—"
                          : `${metric!.returnPercent >= 0 ? "+" : ""}${number.format(metric!.returnPercent)}%`}
                      </strong>
                    </div>
                    <div className="series-legend" aria-label="图表图例">
                      {performanceChartMode === "nlv" ? (
                        <span><i className="nlv" />日结账户净值</span>
                      ) : (
                        <>
                          <span><i className="return" />{method} 收益</span>
                          {visibleBenchmarks.spy && <span><i className="spy" />SPY 总回报</span>}
                          {visibleBenchmarks.qqq && <span><i className="qqq" />QQQ 总回报</span>}
                        </>
                      )}
                    </div>
                    <div className="performance-chart">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={dashboard.series}>
                          <CartesianGrid vertical={false} stroke="var(--grid)" />
                          <XAxis dataKey="date" tickLine={false} axisLine={false} minTickGap={38} />
                          {performanceChartMode === "nlv" ? (
                            <YAxis
                              yAxisId="nlv"
                              tickFormatter={(value) => compactMoney.format(value)}
                              tickLine={false}
                              axisLine={false}
                              width={72}
                              domain={nlvDomain}
                            />
                          ) : (
                            <YAxis
                              yAxisId="return"
                              tickFormatter={(value) => `${number.format(value)}%`}
                              tickLine={false}
                              axisLine={false}
                              width={58}
                            />
                          )}
                          <Tooltip content={<ChartTooltip percent={performanceChartMode === "performance"} />} />
                          {performanceChartMode === "nlv" ? (
                            <>
                              <ReferenceLine
                                yAxisId="nlv"
                                y={dashboard.series[0]?.nlv}
                                stroke="var(--sage)"
                                strokeDasharray="4 4"
                                label={{ value: "期初", position: "insideTopLeft", fill: "var(--sage)", fontSize: 10 }}
                              />
                              <Line
                                yAxisId="nlv"
                                type="monotone"
                                dataKey="nlv"
                                name="净值"
                                stroke="var(--forest)"
                                dot={false}
                                strokeWidth={2.2}
                              />
                            </>
                          ) : (
                            <>
                              <ReferenceLine
                                yAxisId="return"
                                y={0}
                                stroke="var(--sage)"
                                strokeDasharray="4 4"
                                label={{ value: "期初 0%", position: "insideTopLeft", fill: "var(--sage)", fontSize: 10 }}
                              />
                              <Line
                                yAxisId="return"
                                type="monotone"
                                dataKey={seriesKey}
                                name={`${method}收益`}
                                stroke="var(--rust)"
                                dot={false}
                                strokeWidth={1.8}
                              />
                            </>
                          )}
                          {performanceChartMode === "performance" && visibleBenchmarks.spy && (
                            <Line
                              yAxisId="return"
                              type="monotone"
                              dataKey="spy"
                              name="SPY总回报"
                              stroke="var(--ochre)"
                              dot={false}
                              strokeWidth={1.5}
                            />
                          )}
                          {performanceChartMode === "performance" && visibleBenchmarks.qqq && (
                            <Line
                              yAxisId="return"
                              type="monotone"
                              dataKey="qqq"
                              name="QQQ总回报"
                              stroke="var(--benchmark-blue)"
                              dot={false}
                              strokeWidth={1.5}
                            />
                          )}
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  </CardContent>
                </Card>

                <Card className="cost-card">
                  <CardHeader>
                    <CardTitle>成本构成</CardTitle>
                    <CardDescription>费用、税费和融资成本</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="donut-wrap">
                      <ResponsiveContainer width="100%" height={210}>
                        <PieChart>
                          <Pie
                            data={costData}
                            dataKey="value"
                            nameKey="name"
                            innerRadius={58}
                            outerRadius={88}
                            paddingAngle={3}
                          >
                            {costData.map((entry) => (
                              <Cell fill={entry.color} key={entry.name} />
                            ))}
                          </Pie>
                          <Tooltip content={<ChartTooltip />} />
                        </PieChart>
                      </ResponsiveContainer>
                      <div className="donut-center">
                        <strong>{compactMoney.format(totalCosts)}</strong>
                        <span>总成本</span>
                      </div>
                    </div>
                    <div className="legend-list">
                      {costData.map((item) => (
                        <div key={item.name}>
                          <span><i style={{ background: item.color }} />{item.name}</span>
                          <strong>{moneyPrecise.format(item.value)}</strong>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>

                <Card className="flow-card">
                  <CardHeader className="interactive-card-header">
                    <div>
                      <CardTitle>资金进出与损益</CardTitle>
                      <CardDescription>
                        {flowMode === "flows" && "入金为正、出金为负，按日归集"}
                        {flowMode === "dividends" && "现金分红与代息股息，按日归集"}
                        {flowMode === "fees" && "交易佣金以负值显示，按日归集"}
                        {flowMode === "closed" && `${selectedFlowLabel} 的 FIFO 已关闭损益`}
                      </CardDescription>
                    </div>
                    <div className="chart-controls">
                      <div className="compact-toggle" aria-label="图表数据">
                        {flowModes.map((item) => (
                          <button
                            className={flowMode === item.value ? "active" : ""}
                            key={item.value}
                            onClick={() => setFlowMode(item.value)}
                          >
                            {item.label}
                          </button>
                        ))}
                      </div>
                      {(flowMode === "closed" || flowMode === "dividends") && (
                        <Select value={selectedFlowSymbol} onValueChange={flowMode === "closed" ? setClosedSymbol : setDividendSymbol}>
                          <SelectTrigger className="symbol-select" aria-label={flowMode === "closed" ? "选择已关闭标的" : "选择分红标的"}>
                            <SelectValue placeholder="选择标的" />
                          </SelectTrigger>
                          <SelectContent className="app-select-content symbol-select-content" align="end" sideOffset={6}>
                            <SelectItem value="ALL">全部标的</SelectItem>
                            {(flowMode === "closed" ? closedSymbols : dividendSymbols).map((symbol) => (
                              <SelectItem key={symbol} value={symbol}>{symbol}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="flow-chart">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={flowChartData}>
                          <CartesianGrid vertical={false} stroke="var(--grid)" />
                          <XAxis dataKey="date" tickLine={false} axisLine={false} minTickGap={34} />
                          <YAxis
                            tickFormatter={(value) => compactMoney.format(value)}
                            tickLine={false}
                            axisLine={false}
                            width={70}
                          />
                          <Tooltip content={<ChartTooltip />} />
                          {flowMode === "flows" ? (
                            <>
                              <Bar dataKey="deposits" name="入金" fill="var(--forest)" radius={[4, 4, 0, 0]} />
                              <Bar dataKey="withdrawals" name="出金" fill="var(--rust)" radius={[0, 0, 4, 4]} />
                              <Bar dataKey="securityTransfers" name="证券转移" fill="var(--ochre)" radius={[4, 4, 4, 4]} />
                            </>
                          ) : (
                            <Bar
                              dataKey={flowMode === "closed" ? "realized" : "value"}
                              name={
                                flowMode === "dividends"
                                  ? "分红"
                                  : flowMode === "fees"
                                    ? "交易费用"
                                    : `${selectedFlowLabel} 已关闭损益`
                              }
                              radius={[4, 4, 4, 4]}
                            >
                              {flowChartData.map((item, index) => {
                                const value = ("realized" in item ? item.realized : item.value) ?? 0;
                                return <Cell key={`${item.date}-${index}`} fill={value >= 0 ? "var(--forest)" : "var(--rust)"} />;
                              })}
                            </Bar>
                          )}
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                    <div className="flow-totals" aria-label="区间累计">
                      {flowTotals.map((item) => (
                        <div key={item.label}>
                          <span>{item.label}</span>
                          <strong className={item.value > 0 ? "positive" : item.value < 0 ? "negative" : ""}>
                            {signedMoney(item.value)}
                          </strong>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>

                <Card className="closed-card">
                  <CardHeader className="interactive-card-header">
                    <div>
                      <CardTitle>已关闭损益统计</CardTitle>
                      <CardDescription>
                        {closedGrouping === "underlying" ? "同一标的下的全部合约已合并" : "逐合约显示 FIFO Closed Lots"}
                      </CardDescription>
                    </div>
                    <div className="compact-toggle" aria-label="已关闭损益聚合方式">
                      <button
                        className={closedGrouping === "contract" ? "active" : ""}
                        onClick={() => setClosedGrouping("contract")}
                      >
                        按合约
                      </button>
                      <button
                        className={closedGrouping === "underlying" ? "active" : ""}
                        onClick={() => setClosedGrouping("underlying")}
                      >
                        按标的
                      </button>
                    </div>
                  </CardHeader>
                  <CardContent className="closed-list">
                    {topClosed.slice(0, 8).map((item) => (
                      <div className="closed-row" key={item.symbol}>
                        <div className="closed-label">
                          <strong>{item.symbol}</strong>
                          <span>{item.closedLots} 笔 · {item.lastClose}</span>
                        </div>
                        <span className={item.realized >= 0 ? "positive" : "negative"}>
                          {signedMoney(item.realized)}
                        </span>
                        <div className="closed-bar">
                          <i
                            className={item.realized >= 0 ? "gain" : "loss"}
                            style={{ width: `${Math.max(4, (Math.abs(item.realized) / maxClosed) * 100)}%` }}
                          />
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              </section>

              <Tabs defaultValue="holdings" className="detail-tabs">
                <TabsList>
                  <TabsTrigger value="holdings">当前持仓</TabsTrigger>
                  <TabsTrigger value="closed">已关闭交易</TabsTrigger>
                  <TabsTrigger value="cash">现金与收入</TabsTrigger>
                </TabsList>
                <TabsContent value="holdings">
                  <Card>
                    <CardHeader className="table-card-header">
                      <div>
                        <CardTitle>持仓明细</CardTitle>
                        <CardDescription>{dashboard.holdingsDate} · {dashboard.holdings.length} 个合约或标的</CardDescription>
                      </div>
                      <Badge variant="secondary">{account === "ALL" ? "合并账户" : account}</Badge>
                    </CardHeader>
                    <CardContent className="table-scroll">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>标的</TableHead>
                            <TableHead>类型</TableHead>
                            <TableHead className="text-right">数量</TableHead>
                            <TableHead className="text-right">市值</TableHead>
                            <TableHead className="text-right">成本</TableHead>
                            <TableHead className="text-right">未实现盈亏</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {dashboard.holdings.slice(0, 40).map((item) => (
                            <TableRow key={`${item.account}-${item.symbol}`}>
                              <TableCell>
                                <div className="symbol-cell">
                                  <span>{item.underlying.slice(0, 4)}</span>
                                  <div><strong>{item.symbol}</strong><small>{item.account}</small></div>
                                </div>
                              </TableCell>
                              <TableCell><Badge variant="outline">{item.assetClass}</Badge></TableCell>
                              <TableCell className="text-right">{number.format(item.quantity)}</TableCell>
                              <TableCell className="text-right">{moneyPrecise.format(item.marketValue)}</TableCell>
                              <TableCell className="text-right">{moneyPrecise.format(item.costBasis)}</TableCell>
                              <TableCell className={`text-right ${item.unrealized >= 0 ? "positive" : "negative"}`}>
                                <strong>{signedMoney(item.unrealized)}</strong>
                                <small>{item.unrealizedPercent >= 0 ? "+" : ""}{item.unrealizedPercent}%</small>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </CardContent>
                  </Card>
                </TabsContent>
                <TabsContent value="closed">
                  <Card>
                    <CardHeader>
                      <CardTitle>按标的汇总的已关闭交易</CardTitle>
                      <CardDescription>点击区间按钮可重算过去不同时间段的净盈亏</CardDescription>
                    </CardHeader>
                    <CardContent className="table-scroll">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>标的</TableHead>
                            <TableHead>首次关闭</TableHead>
                            <TableHead>最近关闭</TableHead>
                            <TableHead className="text-right">Closed Lots</TableHead>
                            <TableHead className="text-right">净盈亏</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {dashboard.closedTrades.slice(0, 60).map((item) => (
                            <TableRow key={item.symbol}>
                              <TableCell><strong>{item.symbol}</strong></TableCell>
                              <TableCell>{item.firstClose}</TableCell>
                              <TableCell>{item.lastClose}</TableCell>
                              <TableCell className="text-right">{item.closedLots}</TableCell>
                              <TableCell className={`text-right ${item.realized >= 0 ? "positive" : "negative"}`}>
                                <strong>{signedMoney(item.realized)}</strong>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </CardContent>
                  </Card>
                </TabsContent>
                <TabsContent value="cash">
                  <Card>
                    <CardHeader>
                      <CardTitle>资金与收入汇总</CardTitle>
                      <CardDescription>所有数值随上方时间范围联动</CardDescription>
                    </CardHeader>
                    <CardContent className="money-summary">
                      <div><ArrowDownLeft /><span>入金</span><strong>{moneyPrecise.format(dashboard.money.totals.deposits || 0)}</strong></div>
                      <div><ArrowUpRight /><span>出金</span><strong>{moneyPrecise.format(dashboard.money.totals.withdrawals || 0)}</strong></div>
                      <div><HandCoins /><span>分红</span><strong>{moneyPrecise.format(dashboard.money.totals.Dividends || 0)}</strong></div>
                      <div><Coins /><span>代息股息</span><strong>{moneyPrecise.format(dashboard.money.totals["Payment In Lieu Of Dividends"] || 0)}</strong></div>
                      <div><Receipt /><span>佣金</span><strong>{moneyPrecise.format(dashboard.money.totals.commissions || 0)}</strong></div>
                      <div><ShieldCheck /><span>预扣及交易税</span><strong>{moneyPrecise.format(dashboard.money.totals.taxes || 0)}</strong></div>
                    </CardContent>
                  </Card>
                </TabsContent>
              </Tabs>
            </>
          )
        )}

        <footer>
          <Database size={16} />
          <span>{meta ? `${meta.records.toLocaleString()} 条记录 · ${meta.firstDate} 至 ${meta.lastDate}` : "本地数据库"}</span>
        </footer>
      </main>
    </div>
  );
}

export default App;
