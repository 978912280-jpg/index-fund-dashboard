# 指数与基金估值观察台

纯静态仪表台站点：汇总海外主流指数、国内红利低波 ETF 与大盘宽基，每日抓取行情与历史数据，按规则化模型打分并给出五档评级。

> **免责声明**：本项目所有评分均为量化规则输出，仅用于个人研究与记录，**不构成任何投资建议**。市场有风险，决策需自行判断。

## 一、评分模型

综合得分（0–100）由四个维度加权合成，权重与阈值均可在 `config.json` 调整：

| 维度 | 默认权重 | 口径 |
|---|---|---|
| 估值 valuation | 0.35 | 当前收盘价在历史序列中的价格分位反向，越低估得分越高 |
| 技术 technical | 0.25 | 均线趋势 + 20/60 日动量 + RSI(14) 位置 |
| 拥挤度 crowding | 0.15 | 量比（5 日/60 日均量）+ 短期涨幅过热 + 价位分位合成；**反向计入**（越拥挤得分越低） |
| 风险 risk | 0.25 | 年化波动率 + 近一年最大回撤，越小越安全 |

五档评级：

| 得分区间 | 评级 |
|---|---|
| ≥ 80 | 强烈买入 |
| 60 – 80 | 买入 |
| 40 – 60 | 观望 |
| 20 – 40 | 卖出 |
| < 20 | 强烈卖出 |

### 指标口径说明

- 估值：免费端外接口拿不到指数/ETF 的 PE/PB 历史分位，本项目以**价格历史分位**作为估值代理；个股 PE 接口不适用本标的池。
- 拥挤度：为**量价代理口径**（量比、短期涨幅、价位分位）；免费端外不提供 ETF 份额申赎、换手率、主力资金流数据。
- 历史窗口：默认近 750 个交易日（约 3 年），用于分位、均线、动量、波动率与回撤计算。

## 二、标的池

在 `config.json` 的 `items` 中维护，支持增删改。默认分为三类：

- **海外指数**：纳斯达克综合、标普500、道琼斯、日经225、德国DAX、恒生指数
- **红利低波**：红利低波ETF、上证红利ETF、中证红利ETF、深红利ETF
- **大盘宽基**：上证50、沪深300、中证500、中证1000、创业板指

## 三、目录结构

```
fund-dashboard/
├── config.json                 # 标的池、权重、阈值、历史窗口
├── scripts/
│   ├── sources.py              # 多源取数适配层（同花顺/腾讯/新浪/Yahoo）
│   ├── analyze.py              # 指标计算与四维打分、五档评级
│   └── build.py                # 主流程：取数 -> 打分 -> 输出 JSON
├── data/
│   ├── dashboard.json          # 前端主数据（每日生成）
│   └── history/<code>.json     # 完整历史日线快照
├── js/app.js / css/style.css   # 前端逻辑与样式
├── index.html
├── .github/workflows/daily.yml # 定时更新工作流
└── requirements.txt
```

## 四、本地运行

```bash
pip install -r requirements.txt
python3 scripts/build.py
# 生成 data/dashboard.json 后，用静态服务器打开 index.html
python3 -m http.server 8080
```

## 五、数据源与合规

| 范围 | 主源 | 兜底源 | 说明 |
|---|---|---|---|
| 国内指数 / ETF | 同花顺官方 REST（需 API Key） | 腾讯实时 + 新浪历史 | 同花顺 Key 未配置时自动回退免费公开源 |
| 美股指数 | 腾讯实时 + 新浪美股历史 | Yahoo chart | — |
| 港股 / 日经 / DAX | Yahoo chart | — | 依赖海外 runner 网络可达性 |

- 新浪 / 腾讯 / 东财为站点前端接口，Yahoo 条款限制未许可自动抓取；上述免费源**仅供个人研究，不得商用或再分发**。
- 同花顺官方 REST 见 https://fuyao.aicubes.cn/ ，在 API Key 管理页签发 Key。

## 六、部署到 GitHub Pages

1. 将仓库推送到 GitHub。
2. 仓库 **Settings → Pages**，来源选择 `Deploy from a branch`，分支 `main`，目录 `/ (root)`。
3. 站点地址：`https://<用户名>.github.io/<仓库名>/`。

### 配置同花顺 API Key（可选）

仓库 **Settings → Secrets and variables → Actions → New repository secret**：

- Name：`HITHINK_API_KEY`
- Value：签发得到的 API Key

> Key 只存入 Secrets，不写入代码、README 或提交记录。

## 七、定时调度

工作流同时支持三种触发：

1. **外部定时触发（推荐准点）**：用 cron-job.org 等外部服务，每天定点 POST 调用 GitHub `workflow_dispatch` 接口：

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  -H "Authorization: Bearer <PAT>" \
  -H "Accept: application/vnd.github+json" \
  -H "Content-Type: application/json" \
  https://api.github.com/repos/<owner>/<repo>/actions/workflows/daily.yml/dispatches \
  -d '{"ref":"main"}'
# 成功返回 204（空响应），并在 Actions 页面出现 workflow_dispatch 记录
```

- 外部服务时区需显式设为 `Asia/Shanghai`，避免按 UTC 跑偏。
- 触发令牌建议用细粒度 Token，仅授予该仓库 `Actions: Read and write`。

2. **schedule 兜底**：工作流内置 3 个相隔约 30 分钟的 UTC 定时点（对应北京时间 17:05 / 17:35 / 18:05，工作日），防止外部服务失效。
3. **手动触发**：Actions 页面 `Run workflow`。

脚本幂等：数据无变化时不产生提交，外部触发与兜底重复执行不会重复推送。

## 八、已知限制

- 日经225、德国DAX、恒生指数的历史数据依赖 Yahoo，本地国内网络可能不通；需在 Actions 海外 runner 环境验证。
- 指数/ETF 暂无官方 PE/PB 历史分位与份额申赎数据，估值与拥挤度为量价代理口径。
- 海外指数行情与国内基金分属不同市场，交易日历与收盘时间存在差异。
