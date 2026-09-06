import { startTransition, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { formatDate } from "./i18n";
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
  secondary?: React.ReactNode;
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
}: {
  active?: boolean;
  payload?: Array<{ value: number; name: string; color: string }>;
  label?: string;
  percent?: boolean;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <strong>{label && /^\d{4}-\d{2}-\d{2}$/.test(label) ? formatDate(label) : label}</strong>
      {payload.map((item) => (
        <span key={item.name}>
          <i style={{ background: item.color }} />
          {item.name}: {
            percent
              ? `${number.format(Math.abs(item.value) < 0.005 ? 0 : item.value)}%`
              : moneyPrecise.format(Math.abs(item.value) < 0.005 ? 0 : item.value)
          }
        </span>
      ))}
    </div>
  );
}

function App() {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage === "zh-CN" ? "zh-CN" : "en-US";
  const symbolLabel = (symbol: string) => symbol === "未标注标的" ? t("未标注标的") : symbol;
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
  const selectedFlowLabel = selectedFlowSymbol === "ALL" ? t("全部标的") : symbolLabel(selectedFlowSymbol);
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
          { label: t("累计入金"), value: dashboard.money.totals.deposits || 0 },
          { label: t("累计出金"), value: -(dashboard.money.totals.withdrawals || 0) },
          { label: t("证券净转移"), value: dashboard.metrics.securityNetTransfers },
          { label: t("净外部流入"), value: dashboard.metrics.netContributions },
        ]
      : flowMode === "dividends"
        ? [{ label: t("dividendTotal", { symbol: selectedFlowLabel }), value: dividendTotal }]
        : flowMode === "fees"
          ? [{ label: t("累计交易费用"), value: -(dashboard.money.totals.commissions || 0) }]
          : [{ label: t("fifoTotal", { symbol: selectedFlowLabel }), value: closedPnlTotal }]
    : [];
  const costData = dashboard
    ? [
        { name: t("佣金"), value: dashboard.money.totals.commissions || 0, color: "var(--ink)" },
        { name: t("融资利息"), value: dashboard.money.totals.interestPaid || 0, color: "var(--rust)" },
        { name: t("税费"), value: dashboard.money.totals.taxes || 0, color: "var(--ochre)" },
        { name: t("其他"), value: dashboard.money.totals.otherFees || 0, color: "var(--sage)" },
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
          <button className="nav-item active" aria-label={t("总览")}>
            <Gauge size={21} />
          </button>
          <button className="nav-item" aria-label={t("持仓")}>
            <Wallet size={21} />
          </button>
          <button className="nav-item" aria-label={t("交易")}>
            <ChartLineUp size={21} />
          </button>
          <button className="nav-item" aria-label={t("现金流")}>
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
            <h1>{t("资产监控台")}</h1>
            <p className="subtitle">
              {dashboard ? `${formatDate(dashboard.range.from)} — ${formatDate(dashboard.range.to)}` : t("正在连接本地IBKR数据")}
            </p>
          </div>
          <div className="header-actions">
            <div className="compact-toggle language-toggle" role="group" aria-label={t("language")}>
              <button type="button" lang="en" aria-pressed={locale === "en-US"} className={locale === "en-US" ? "active" : ""} onClick={() => void i18n.changeLanguage("en")}>EN</button>
              <button type="button" lang="zh-CN" aria-pressed={locale === "zh-CN"} className={locale === "zh-CN" ? "active" : ""} onClick={() => void i18n.changeLanguage("zh-CN")}>中文</button>
            </div>
            <div className="sync-pill">
              <span className="sync-dot" />
              {meta?.lastSync ? t("synced", { date: new Date(meta.lastSync).toLocaleString(locale) }) : t("等待同步")}
            </div>
            <Select value={account} onValueChange={setAccount}>
              <SelectTrigger className="account-select" aria-label={t("全部账户")}>
                <SelectValue placeholder={t("全部账户")} />
              </SelectTrigger>
              <SelectContent className="app-select-content" align="end" sideOffset={6}>
                <SelectItem value="ALL">{t("全部账户")}</SelectItem>
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
                title={t(`rangeTitle.${item}`)}
                aria-pressed={range === item}
              >
                {t(`range.${item}`)}
              </Button>
            ))}
          </div>
          {range === "CUSTOM" && (
            <div className="custom-dates">
              <input
                type="date"
                value={customFrom}
                aria-label={t("startDate")}
                lang={locale}
                min={meta?.firstDate}
                max={customTo || meta?.lastDate}
                onChange={(event) => setCustomFrom(event.target.value)}
              />
              <span>{t("至")}</span>
              <input
                type="date"
                value={customTo}
                aria-label={t("endDate")}
                lang={locale}
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
                {loading && range === "CUSTOM" ? t("应用中") : t("应用")}
              </Button>
              {!customFrom && (
                <span className="custom-date-hint">
                  {t("请选择开始日期")}
                </span>
              )}
              {customFrom && !customTo && customDatesValid && (
                <span className="custom-date-hint">
                  {t("latestEnd", { date: formatDate(meta?.lastDate) })}
                </span>
              )}
              {customFrom && effectiveCustomTo && !customDatesValid && (
                <span className="custom-date-hint error">{t("结束日期不能早于开始日期")}</span>
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
                {t(item.label)}
              </Button>
            ))}
          </div>
        </section>

        {error && <div className="error-banner" role="alert">{t("loadError")}<details><summary>{t("errorDetails")}</summary>{error}</details></div>}

        {loading && !dashboard ? (
          <div className="loading-grid" role="status" aria-label={t("loading")}>
            <span className="sr-only">{t("loading")}</span>
            {Array.from({ length: 8 }).map((_, index) => (
              <Skeleton className="h-32 rounded-2xl" key={index} />
            ))}
          </div>
        ) : (
          dashboard && (
            <>
              <section className="metrics-grid">
                <MetricCard
                  label={t("日结账户净值")}
                  value={money.format(metric!.nlv)}
                  detail={t("valueDetail", { date: formatDate(dashboard.range.to), change: signedMoney(metric!.nlvChange) })}
                  tone={metric!.nlvChange >= 0 ? "positive" : "negative"}
                  icon={<Bank size={20} />}
                />
                <MetricCard
                  label={t("methodReturn", { method: method === "SIMPLE" ? t("简单收益") : method })}
                  value={
                    metric!.returnPercent == null ? "—" : `${metric!.returnPercent >= 0 ? "+" : ""}${number.format(metric!.returnPercent)}%`
                  }
                  secondary={<>
                    <span>{t("annualized", { value: metric!.annualizedReturnPercent == null ? "—" : `${metric!.annualizedReturnPercent >= 0 ? "+" : ""}${number.format(metric!.annualizedReturnPercent)}%` })}</span>
                    <span title={t("xirrDescription")}>{t("xirr", { value: metric!.xirrPercent == null ? "—" : `${metric!.xirrPercent >= 0 ? "+" : ""}${number.format(metric!.xirrPercent)}%` })}</span>
                  </>}
                  detail={method === "SIMPLE" ? t("净值变化，含资金进出影响") : t("已调整外部资金流")}
                  tone={(metric!.returnPercent ?? 0) >= 0 ? "positive" : "negative"}
                  icon={<ChartLineUp size={20} />}
                />
                <MetricCard
                  label={t("区间总损益")}
                  value={mtmComplete ? signedMoney(metric!.totalPnl) : "—"}
                  detail={
                    mtmComplete
                      ? t("直接汇总 MTM、收入及成本")
                      : t("mtmCoverage", { date: formatDate(dashboard.mtm.coverageTo) })
                  }
                  tone={!mtmComplete ? "neutral" : metric!.totalPnl >= 0 ? "positive" : "negative"}
                  icon={metric!.totalPnl >= 0 ? <TrendUp size={20} /> : <TrendDown size={20} />}
                />
                <MetricCard
                  label={t("已实现盈亏")}
                  value={signedMoney(metric!.realized)}
                  detail={t("FIFO Closed Lots")}
                  tone={metric!.realized >= 0 ? "positive" : "negative"}
                  icon={metric!.realized >= 0 ? <TrendUp size={20} /> : <TrendDown size={20} />}
                />
                <MetricCard
                  label={t("未实现盈亏（期末）")}
                  value={signedMoney(metric!.unrealized)}
                  detail={t("holdingsDate", { date: formatDate(dashboard.holdingsDate) })}
                  tone={metric!.unrealized >= 0 ? "positive" : "negative"}
                  icon={<Coins size={20} />}
                />
                <MetricCard
                  label={t("净外部流入")}
                  value={signedMoney(metric!.netContributions)}
                  detail={t("contributionDetail", { cash: signedMoney(metric!.cashNetContributions), securities: signedMoney(metric!.securityNetTransfers) })}
                  tone="neutral"
                  icon={<ArrowDownLeft size={20} />}
                />
                <MetricCard
                  label={t("收入")}
                  value={moneyPrecise.format(metric!.income)}
                  detail={t("分红及利息收入")}
                  tone="positive"
                  icon={<HandCoins size={20} />}
                />
                <MetricCard
                  label={t("交易费用")}
                  value={moneyPrecise.format(metric!.fees)}
                  detail={t("已计入 FIFO 成本，仅作费用统计")}
                  tone="negative"
                  icon={<Receipt size={20} />}
                />
                <MetricCard
                  label={t("融资与税费")}
                  value={moneyPrecise.format(metric!.financing + metric!.taxes)}
                  detail={t("融资利息、预扣税及交易税")}
                  tone="negative"
                  icon={<ShieldCheck size={20} />}
                />
              </section>

              {mtmComplete && (
                <section className="direct-pnl-panel" aria-label={t("区间总损益构成")}>
                  <div className="direct-pnl-heading">
                    <div>
                      <strong>{t("区间总损益构成")}</strong>
                      <span>{t("含应计利息及股息变动，分项分别四舍五入至分")}</span>
                    </div>
                  </div>
                  <div className="direct-pnl-equation">
                    {dashboard.pnlComponents.map((item, index) => (
                      <div key={item.label}><i>{index ? "+" : ""}</i><span>{t(item.label)}</span><strong>{signedMoney(item.value)}</strong></div>
                    ))}
                    <div className="result"><i>=</i><span>{t("区间总损益")}</span><strong>{signedMoney(metric!.totalPnl)}</strong></div>
                  </div>
                </section>
              )}

              <section className="dashboard-grid">
                <Card className="performance-card">
                  <CardHeader className="chart-header">
                    <div>
                      <CardTitle>{t("账户净值与业绩")}</CardTitle>
                      <CardDescription>{t("IBKR Statement Mark 日结口径，不代表可成交平仓价值")}</CardDescription>
                    </div>
                    <div className="performance-actions">
                      <Badge variant="outline">{method}</Badge>
                      <div className="compact-toggle" aria-label={t("图表模式")}>
                        <button
                          className={performanceChartMode === "performance" ? "active" : ""}
                          onClick={() => setPerformanceChartMode("performance")}
                        >
                          {t("业绩对比")}
                        </button>
                        <button
                          className={performanceChartMode === "nlv" ? "active" : ""}
                          onClick={() => setPerformanceChartMode("nlv")}
                        >
                          {t("净值走势")}
                        </button>
                      </div>
                      {performanceChartMode === "performance" && (
                        <div className="compact-toggle benchmark-toggle" aria-label={t("基准指数")}>
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
                    <div className="series-legend" aria-label={t("图表图例")}>
                      {performanceChartMode === "nlv" ? (
                        <span><i className="nlv" />{t("日结账户净值")}</span>
                      ) : (
                        <>
                          <span><i className="return" />{t("methodReturn", { method: method === "SIMPLE" ? t("简单收益") : method })}</span>
                          {visibleBenchmarks.spy && <span><i className="spy" />{t("SPY 总回报")}</span>}
                          {visibleBenchmarks.qqq && <span><i className="qqq" />{t("QQQ 总回报")}</span>}
                        </>
                      )}
                    </div>
                    {dashboard.series.length === 0 && <p className="empty-state">{t("empty")}</p>}
                    <div className="performance-chart">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={dashboard.series}>
                          <CartesianGrid vertical={false} stroke="var(--grid)" />
                          <XAxis tickFormatter={(value: string) => formatDate(value, true)} dataKey="date" tickLine={false} axisLine={false} minTickGap={38} />
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
                                label={{ value: t("期初"), position: "insideTopLeft", fill: "var(--sage)", fontSize: 10 }}
                              />
                              <Line
                                yAxisId="nlv"
                                type="monotone"
                                dataKey="nlv"
                                name={t("净值")}
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
                                label={{ value: t("期初 0%"), position: "insideTopLeft", fill: "var(--sage)", fontSize: 10 }}
                              />
                              <Line
                                yAxisId="return"
                                type="monotone"
                                dataKey={seriesKey}
                                name={t("methodReturn", { method: method === "SIMPLE" ? t("简单收益") : method })}
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
                              name={t("SPY总回报")}
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
                              name={t("QQQ总回报")}
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
                    <CardTitle>{t("成本构成")}</CardTitle>
                    <CardDescription>{t("费用、税费和融资成本")}</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {costData.length === 0 && <p className="empty-state">{t("empty")}</p>}
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
                        <span>{t("总成本")}</span>
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
                      <CardTitle>{t("资金进出与损益")}</CardTitle>
                      <CardDescription>
                        {flowMode === "flows" && t("入金为正、出金为负，按日归集")}
                        {flowMode === "dividends" && t("现金分红与代息股息，按日归集")}
                        {flowMode === "fees" && t("交易佣金以负值显示，按日归集")}
                        {flowMode === "closed" && t("closedDescription", { symbol: selectedFlowLabel })}
                      </CardDescription>
                    </div>
                    <div className="chart-controls">
                      <div className="compact-toggle" aria-label={t("图表数据")}>
                        {flowModes.map((item) => (
                          <button
                            className={flowMode === item.value ? "active" : ""}
                            key={item.value}
                            onClick={() => setFlowMode(item.value)}
                          >
                            {t(item.label)}
                          </button>
                        ))}
                      </div>
                      {(flowMode === "closed" || flowMode === "dividends") && (
                        <Select value={selectedFlowSymbol} onValueChange={flowMode === "closed" ? setClosedSymbol : setDividendSymbol}>
                          <SelectTrigger className="symbol-select" aria-label={flowMode === "closed" ? t("选择已关闭标的") : t("选择分红标的")}>
                            <SelectValue placeholder={t("选择标的")} />
                          </SelectTrigger>
                          <SelectContent className="app-select-content symbol-select-content" align="end" sideOffset={6}>
                            <SelectItem value="ALL">{t("全部标的")}</SelectItem>
                            {(flowMode === "closed" ? closedSymbols : dividendSymbols).map((symbol) => (
                              <SelectItem key={symbol} value={symbol}>{symbolLabel(symbol)}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent>
                    {flowChartData.length === 0 && <p className="empty-state">{t("empty")}</p>}
                    <div className="flow-chart">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={flowChartData}>
                          <CartesianGrid vertical={false} stroke="var(--grid)" />
                          <XAxis tickFormatter={(value: string) => formatDate(value, true)} dataKey="date" tickLine={false} axisLine={false} minTickGap={34} />
                          <YAxis
                            tickFormatter={(value) => compactMoney.format(value)}
                            tickLine={false}
                            axisLine={false}
                            width={70}
                          />
                          <Tooltip content={<ChartTooltip />} />
                          {flowMode === "flows" ? (
                            <>
                              <Bar dataKey="deposits" name={t("入金")} fill="var(--forest)" radius={[4, 4, 0, 0]} />
                              <Bar dataKey="withdrawals" name={t("出金")} fill="var(--rust)" radius={[0, 0, 4, 4]} />
                              <Bar dataKey="securityTransfers" name={t("证券转移")} fill="var(--ochre)" radius={[4, 4, 4, 4]} />
                            </>
                          ) : (
                            <Bar
                              dataKey={flowMode === "closed" ? "realized" : "value"}
                              name={
                                flowMode === "dividends"
                                  ? t("分红")
                                  : flowMode === "fees"
                                    ? t("交易费用")
                                    : t("closedLabel", { symbol: selectedFlowLabel })
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
                    <div className="flow-totals" aria-label={t("区间累计")}>
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
                      <CardTitle>{t("已关闭损益统计")}</CardTitle>
                      <CardDescription>
                        {closedGrouping === "underlying" ? t("同一标的下的全部合约已合并") : t("逐合约显示 FIFO Closed Lots")}
                      </CardDescription>
                    </div>
                    <div className="compact-toggle" aria-label={t("已关闭损益聚合方式")}>
                      <button
                        className={closedGrouping === "contract" ? "active" : ""}
                        onClick={() => setClosedGrouping("contract")}
                      >
                        {t("按合约")}
                      </button>
                      <button
                        className={closedGrouping === "underlying" ? "active" : ""}
                        onClick={() => setClosedGrouping("underlying")}
                      >
                        {t("按标的")}
                      </button>
                    </div>
                  </CardHeader>
                  <CardContent className="closed-list">
                    {topClosed.length === 0 && <p className="empty-state">{t("empty")}</p>}
                    {topClosed.slice(0, 8).map((item) => (
                      <div className="closed-row" key={symbolLabel(item.symbol)}>
                        <div className="closed-label">
                          <strong>{symbolLabel(item.symbol)}</strong>
                          <span>{t("closedCount", { count: item.closedLots, date: formatDate(item.lastClose) })}</span>
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
                  <TabsTrigger value="holdings">{t("当前持仓")}</TabsTrigger>
                  <TabsTrigger value="closed">{t("已关闭交易")}</TabsTrigger>
                  <TabsTrigger value="cash">{t("现金与收入")}</TabsTrigger>
                </TabsList>
                <TabsContent value="holdings">
                  <Card>
                    <CardHeader className="table-card-header">
                      <div>
                        <CardTitle>{t("持仓明细")}</CardTitle>
                        <CardDescription>{t("holdingsCount", { date: formatDate(dashboard.holdingsDate), count: dashboard.holdings.length })}</CardDescription>
                      </div>
                      <Badge variant="secondary">{account === "ALL" ? t("合并账户") : account}</Badge>
                    </CardHeader>
                    <CardContent className="table-scroll">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>{t("标的")}</TableHead>
                            <TableHead>{t("类型")}</TableHead>
                            <TableHead className="text-right">{t("数量")}</TableHead>
                            <TableHead className="text-right">{t("市值")}</TableHead>
                            <TableHead className="text-right">{t("成本")}</TableHead>
                            <TableHead className="text-right">{t("未实现盈亏")}</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {dashboard.holdings.length === 0 && <TableRow><TableCell colSpan={6} className="empty-state">{t("empty")}</TableCell></TableRow>}
                          {dashboard.holdings.slice(0, 40).map((item) => (
                            <TableRow key={`${item.account}-${symbolLabel(item.symbol)}`}>
                              <TableCell>
                                <div className="symbol-cell">
                                  <span>{item.underlying === "未标注标的" ? "—" : item.underlying.slice(0, 4)}</span>
                                  <div><strong>{symbolLabel(item.symbol)}</strong><small>{item.account}</small></div>
                                </div>
                              </TableCell>
                              <TableCell><Badge variant="outline">{t(`asset.${item.assetClass}`, { defaultValue: item.assetClass })}</Badge></TableCell>
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
                      <CardTitle>{t("按标的汇总的已关闭交易")}</CardTitle>
                      <CardDescription>{t("点击区间按钮可重算过去不同时间段的净盈亏")}</CardDescription>
                    </CardHeader>
                    <CardContent className="table-scroll">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>{t("标的")}</TableHead>
                            <TableHead>{t("首次关闭")}</TableHead>
                            <TableHead>{t("最近关闭")}</TableHead>
                            <TableHead className="text-right">{t("Closed Lots")}</TableHead>
                            <TableHead className="text-right">{t("净盈亏")}</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {dashboard.closedTrades.length === 0 && <TableRow><TableCell colSpan={5} className="empty-state">{t("empty")}</TableCell></TableRow>}
                          {dashboard.closedTrades.slice(0, 60).map((item) => (
                            <TableRow key={symbolLabel(item.symbol)}>
                              <TableCell><strong>{symbolLabel(item.symbol)}</strong></TableCell>
                              <TableCell>{formatDate(item.firstClose)}</TableCell>
                              <TableCell>{formatDate(item.lastClose)}</TableCell>
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
                      <CardTitle>{t("资金与收入汇总")}</CardTitle>
                      <CardDescription>{t("所有数值随上方时间范围联动")}</CardDescription>
                    </CardHeader>
                    <CardContent className="money-summary">
                      <div><ArrowDownLeft /><span>{t("入金")}</span><strong>{moneyPrecise.format(dashboard.money.totals.deposits || 0)}</strong></div>
                      <div><ArrowUpRight /><span>{t("出金")}</span><strong>{moneyPrecise.format(dashboard.money.totals.withdrawals || 0)}</strong></div>
                      <div><HandCoins /><span>{t("分红")}</span><strong>{moneyPrecise.format(dashboard.money.totals.Dividends || 0)}</strong></div>
                      <div><Coins /><span>{t("代息股息")}</span><strong>{moneyPrecise.format(dashboard.money.totals["Payment In Lieu Of Dividends"] || 0)}</strong></div>
                      <div><Receipt /><span>{t("佣金")}</span><strong>{moneyPrecise.format(dashboard.money.totals.commissions || 0)}</strong></div>
                      <div><ShieldCheck /><span>{t("预扣及交易税")}</span><strong>{moneyPrecise.format(dashboard.money.totals.taxes || 0)}</strong></div>
                    </CardContent>
                  </Card>
                </TabsContent>
              </Tabs>
            </>
          )
        )}

        <footer>
          <Database size={16} />
          <span>{meta ? t("databaseSummary", { count: meta.records, from: formatDate(meta.firstDate), to: formatDate(meta.lastDate) }) : t("本地数据库")}</span>
        </footer>
      </main>
    </div>
  );
}

export default App;
