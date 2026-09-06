"""Local, synthetic-only preview of the production dashboard (no external I/O)."""

import argparse
import json
import math
from datetime import date, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


DIST = Path(__file__).resolve().parents[1] / "dist"
ACCOUNTS = ["DEMO-ALPHA", "DEMO-BETA"]
FIRST, LAST = date(2024, 12, 31), date(2025, 12, 31)
DATES = [FIRST + timedelta(days=i) for i in range((LAST - FIRST).days + 1)
         if (FIRST + timedelta(days=i)).weekday() < 5]
META = dict(accounts=ACCOUNTS, firstDate=FIRST.isoformat(), lastDate=LAST.isoformat(),
            lastSync="2025-12-31T22:00:00Z", reports=24, records=1842,
            database="Synthetic demo")
DIVIDENDS = {max(d for d in DATES if d.year == 2025 and d.month == month):
             sum(d.year == 2025 and d.month == month for d in DATES) * 2.4
             for month in range(1, 13)}
TRADE_WEIGHTS = [("NVDA", .42), ("MSFT", .31), ("AAPL", .22), ("VTI", .15), ("AMD", -.10)]


def level(index, growth):
    t = index / (len(DATES) - 1)
    return 1 + growth * t + .014 * math.sin(6 * math.pi * t) + .006 * math.sin(22 * math.pi * t)


def dashboard(query):
    preset, account = query.get("range", "1Y"), query.get("account", "ALL")
    method = query.get("method", "TWR")
    if account not in ["ALL", *ACCOUNTS] or method not in ["SIMPLE", "TWR", "MWR"]:
        raise ValueError("Unknown demo account or return method")
    starts = {"ALL": FIRST, "1Y": FIRST, "YTD": FIRST, "MTD": date(2025, 11, 28),
              "1W": LAST - timedelta(days=7), "1M": LAST - timedelta(days=31),
              "3M": LAST - timedelta(days=92), "CUSTOM": FIRST}
    if preset not in starts:
        raise ValueError("Unknown range")
    start = date.fromisoformat(query.get("from", FIRST.isoformat())) if preset == "CUSTOM" else starts[preset]
    end = date.fromisoformat(query.get("to", LAST.isoformat())) if preset == "CUSTOM" else LAST
    indexes = [i for i, day in enumerate(DATES) if start <= day <= end]
    if len(indexes) < 2:
        raise ValueError("Select at least two demo trading days in 2025")
    a, b = indexes[0], indexes[-1]
    scale = {"ALL": 1, "DEMO-ALPHA": .6, "DEMO-BETA": .4}[account]
    capital = 100000 * scale
    initial, final = capital * level(a, .214), capital * level(b, .214)
    profit, days = final - initial, b - a
    dividends = sum(DIVIDENDS.get(DATES[i], 0) for i in indexes[1:]) * scale
    income = dividends + days * .4 * scale
    fees, financing, taxes = (days * scale * v for v in (.32, .1, .42))
    realized = profit * .35 - income + fees + financing + taxes
    unrealized = 4000 * scale + (final - capital) * .65
    ret = (final / initial - 1) * 100
    elapsed = (DATES[b] - DATES[a]).days
    series = []
    for i in indexes:
        r = (level(i, .214) / level(a, .214) - 1) * 100
        series.append(dict(date=DATES[i].isoformat(), nlv=capital * level(i, .214), flow=0,
                           simple=r, twr=r, mwr=r,
                           spy=(level(i, .168) / level(a, .168) - 1) * 100,
                           qqq=(level(i, .246) / level(a, .246) - 1) * 100))
    holdings = []
    selected = ACCOUNTS if account == "ALL" else [account]
    for name in selected:
        share = {"DEMO-ALPHA": .6, "DEMO-BETA": .4}[name] / scale
        for symbol, weight, price in [("AAPL", .23, 240), ("MSFT", .27, 480),
                                       ("NVDA", .18, 160), ("VTI", .32, 300)]:
            value, gain = final * .88 * share * weight, unrealized * share * weight
            cost = value - gain
            holdings.append(dict(account=name, symbol=symbol, underlying=symbol,
                                 assetClass="STK", currency="USD", quantity=value / price,
                                 markPrice=price, marketValue=value, costBasis=cost,
                                 unrealized=gain, unrealizedPercent=gain / cost * 100))
    daily, closed = [], []
    for i in indexes[1:]:
        day = DATES[i].isoformat()
        dividend = DIVIDENDS.get(DATES[i], 0) * scale
        daily.append(dict(date=day, deposits=0, withdrawals=0, securityTransfers=0,
                          dividends=dividend, interestIncome=.4 * scale, income=dividend + .4 * scale,
                          commissions=.32 * scale, financing=.1 * scale, fees=0, taxes=.42 * scale))
        gain = capital * (level(i, .214) - level(i - 1, .214))
        closed.extend(dict(date=day, symbol=symbol, realized=(gain * .35 - dividend + .44 * scale) * weight)
                      for symbol, weight in TRADE_WEIGHTS)
    totals = {"deposits": 0, "withdrawals": 0, "securityTransfers": 0,
              "Dividends": dividends, "Payment In Lieu Of Dividends": 0,
              "interestIncome": days * .4 * scale, "income": income,
              "commissions": fees, "interestPaid": financing, "otherFees": 0, "taxes": taxes}
    trades = [dict(symbol=symbol, realized=realized * weight, closedLots=days,
                   firstClose=DATES[a + 1].isoformat(), lastClose=DATES[b].isoformat())
              for symbol, weight in TRADE_WEIGHTS]
    return dict(
        range=dict(preset=preset, **{"from": DATES[a].isoformat(), "to": DATES[b].isoformat()}),
        account=account, algorithm=method,
        metrics=dict(nlv=final, nlvChange=profit, startingNlv=initial, returnPercent=ret,
                     annualizedReturnPercent=((final / initial) ** (365.25 / elapsed) - 1) * 100,
                     xirrPercent=((final / initial) ** (365 / elapsed) - 1) * 100,
                     realized=realized, unrealized=unrealized, unrealizedChange=profit * .65,
                     totalPnl=profit, netContributions=0, cashNetContributions=0,
                     securityNetTransfers=0, income=income, fees=fees,
                     financing=financing, taxes=taxes),
        returns=dict(simple=ret, twr=ret, mwr=ret),
        benchmarkReturns=dict(spy=series[-1]["spy"], qqq=series[-1]["qqq"]),
        pnlComponents=[dict(label=label, value=value) for label, value in
                       [("持仓 MTM", profit * .65), ("交易 MTM", realized), ("交易佣金", -fees),
                        ("分红及代息股息", dividends), ("净利息", days * .4 * scale - financing),
                        ("税费", -taxes), ("其他费用", 0), ("应计利息变动", 0), ("应计股息变动", 0)]],
        series=series, holdingsDate=DATES[b].isoformat(), holdings=holdings,
        closedTrades=trades, closedTradeContracts=trades, closedPnlDaily=closed,
        mtm=dict(coverageFrom=DATES[a].isoformat(), coverageTo=DATES[b].isoformat(), days=days,
                 priorOpen=profit * .65, transactions=realized, commissions=-fees,
                 otherWithAccruals=income - financing - taxes,
                 reportedTotal=profit, componentTotal=profit, difference=0),
        money=dict(totals=totals, daily=daily,
                   dividendsBySymbol=[dict(date=row["date"], symbol="VTI", amount=row["dividends"])
                                      for row in daily if row["dividends"]]))


class PreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path.startswith("/api/"):
            try:
                if url.path == "/api/meta":
                    data = META
                elif url.path == "/api/dashboard":
                    data = dashboard({k: v[-1] for k, v in parse_qs(url.query).items()})
                else:
                    self.send_error(404)
                    return
                status = 200
            except ValueError as error:
                data, status = {"error": str(error)}, 400
            payload = json.dumps(data, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        # Only production assets under dist may be served, including resolved symlinks.
        target = Path(self.translate_path(url.path)).resolve()
        if not target.is_relative_to(DIST.resolve()) or not target.is_file() and url.path != "/":
            self.send_error(404)
            return
        super().do_GET()

    def do_HEAD(self):
        self.send_error(405, "Use GET for this preview")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4180, help="Local HTTP port (default: 4180)")
    args = parser.parse_args()
    if not (DIST / "index.html").is_file():
        parser.error("Build the production frontend first; dist/index.html is missing")
    with ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler) as server:
        print(f"Synthetic demo only: http://127.0.0.1:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
