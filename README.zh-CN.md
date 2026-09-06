# Portfolio Analyze

[English](README.md) | **简体中文**

面向 Interactive Brokers Flex 报表的本地投资组合监控面板，使用 React、
Wealthfolio UI、Recharts 和 Python/SQLite 构建。支持中文与英文，账户数据保存在本机。

![Portfolio Analyze 面板，使用完全虚构的演示数据](docs/images/overview-en.png)

*上图完全由虚构数据生成，不包含真实账户、持仓、交易或投资业绩。*

## 功能

- 中英文切换，自动记住语言偏好。
- 账户筛选，以及 1W、MTD、1M、3M、YTD、1Y、ALL 和自定义时间范围。
- 日结净值、TWR、Modified Dietz 收益及年化收益。
- SPY、QQQ 基准对比，收益曲线共用百分比坐标轴。
- 持仓明细、FIFO 已关闭损益、分红和现金流水。
- 分红和已关闭损益支持按标的筛选，并显示区间累计额。
- 本地 SQLite 存储与命令行导入、同步入口。

## 查看演示

需要 Python 3.10+ 和 Node.js 22.12+，不需要 IBKR 凭据。

```powershell
npm ci
npm run build
python scripts/demo_preview.py
```

打开 `http://127.0.0.1:4180`。演示使用同一个前端及确定生成的虚构数据，
不会访问真实账户数据库或外部服务。通过页头切换语言，按 Ctrl+C 停止预览服务。

## Windows 本地运行

1. 执行 `npm ci` 安装依赖。
2. 将 `scripts/.env.example` 复制为 `scripts/.env`，填写自己的 Flex Web Service
   Token 和 Activity Flex Query ID。使用一个 Query 选择要查看的账户，输出格式为
   XML，并开启按日拆分。
3. 导入或同步账户数据：

   ```powershell
   python scripts/ibkr_analyzer.py doctor
   python scripts/ibkr_analyzer.py sync-history --days 365 --chunk-days 29
   ```

4. 运行 `sync-benchmarks.bat` 下载基准行情。
5. 双击 `start.bat`，打开 `http://127.0.0.1:4173`。

指定历史同步区间：

```powershell
python scripts/ibkr_analyzer.py sync --from 2024-01-01 --to 2024-12-31 --chunk-days 29
```

导入已归档的 XML 报表：

```powershell
python scripts/ibkr_analyzer.py import-xml --help
```

Flex Query 需要包含账户信息、现金报告、现金交易、未平仓持仓、交易及 Closed Lots、
金融工具信息、转移、佣金明细、交易费用/税费、基础货币净资产值，以及基础货币 MTM
业绩汇总。请同时开启汇率和按日拆分。当日没有发生业务的 section 可以为空。

## 计算口径

收益卡片并列显示折算年化和 XIRR。XIRR 按实际日期的净投入、期初和期末净值求解，
采用 Actual/365 天数口径，本身即为年化资金加权收益率。期初估值日的资金流已经包含在
期初净值中，不再重复记为投入。同日区间或未找到明确解时显示空值。

账户净值采用 IBKR 日结标记价，可能与实际可成交平仓价值不同。已关闭交易使用 Flex
FIFO 盈亏。收益算法包含简单净值变化、TWR 和 Modified Dietz；年化按实际区间天数折算。

基准使用 Yahoo Finance 复权收盘价。其图表接口为非正式接口，服务可能发生变化。

## 命令行与定时同步

```powershell
python scripts/ibkr_analyzer.py --help
python scripts/daily_sync.py
python backend/benchmarks.py sync
python backend/benchmarks.py status
python launcher.py --no-browser
npm run build
```

`daily_sync.py` 同步截至前一工作日的最近八个自然日，并跳过已覆盖日期。长时间中断后，
应通过 `sync --from ... --to ...` 补齐更早缺口。可在 Windows 任务计划程序中配置这些命令；
克隆仓库不会自动安装系统任务。

## 配置与数据位置

- Flex 数据库：`%APPDATA%/ibkr-analyzer/records.sqlite3`；原始报表位于同目录下的 `raw/`。
- 基准数据库：`%APPDATA%/portfolio-analyze/benchmarks.sqlite3`。
- `IBKR_ANALYZER_ENV`：指定 Flex 配置文件，导入器和面板均支持。
  存在 `scripts/.env` 时，`start.bat` 会选用该文件。
- `IBKR_ANALYZER_HOME`：指定导入器数据目录。
- `IBKR_ANALYZER_DB`：指定面板读取的账户数据库。
- `PORTFOLIO_BENCHMARK_DB`：指定面板读取的基准数据库；相应下载命令为
  `python backend/benchmarks.py --database PATH sync`。

面板默认配置路径也支持本机 `~/.codex/skills/ibkr-analyzer/scripts/.env`。
使用仓库内的脚本与 `scripts/.env` 时，无需 AI agent。

凭据、报表、数据库、日志、真实截图和个人分析均排除在版本控制之外。仓库不分发真实
账户数据或已填写的配置。服务默认仅监听本机；请勿将含账户数据的面板直接暴露到未设置
认证的公网环境。

## 许可证

MIT。参见 [LICENSE](LICENSE) 和[第三方声明](THIRD_PARTY_NOTICES.md)。
