# 个股雷达 · 公司深度分析 / Stock Radar

一个**完全独立、可直接在浏览器打开**的 A 股公司深度分析仪表盘。点击任一公司，即弹出该公司的：

1. **公司简介**（业务简介 + 管理层动向）
2. **各项业务营收占比**（主营构成饼图，收入占比 / 毛利率，增速异常项自动标注）
3. **营收增速**（总体年度同比 + 各分部增速 + 近 8 个单季营收 + 归母净利润）
4. **现金流分析**（经营 / 自由现金流 / 资本开支 / 收购 / 分红一图看清"钱去哪了"，并用规则引擎自动提示盈利质量与"漏洞"要点，例如大额收购、借钱分红、增收不增利）
5. **风险分析**（资产负债率 / 流动比率 / 速动比率 / 毛利率 / ROE / 审计意见 + 预警）
6. **政策分析**（按所属行业的政策要点静态梳理，中英双语）
7. **利好 vs 利空消息 + 期权博弈**（新闻按关键词粗分类；A 股无个股期权，用**融资融券**多空杠杆作代理）
8. **暗池数据**（A 股无暗池，用**大宗交易**——最接近"场外大额成交"的公开数据作代理：折溢价 / 机构专用席位 / 占流通市值）
9. **企业相关重要新闻**（东财个股新闻，点击直达原文）

外加：**日 / 夜 / 跟随系统** 三态主题切换、**中 / 英文** 一键切换、每只股票内嵌 **TradingView** 实时交互图与概览页链接。

> ⚠️ 仅对公开财务/行情数据做自动化整理与展示，**不构成任何投资建议**。暗池以大宗交易近似、期权博弈以融资融券近似，均为 A 股代理指标。所有数据需人工复核，使用者自负盈亏。

## 直接打开

- 本地：双击 `docs/index.html` 即可（数据来自同目录的 `data.js`，无需服务器）。
- GitHub Pages：把仓库 Pages 源设为 `/docs`，访问 `https://<用户名>.github.io/stock-radar/` 直接打开。

## 目录结构

```
stock-radar/
├─ docs/                 # 直接打开 / GitHub Pages 根目录
│  ├─ index.html         # 仪表盘 (自包含: Tailwind + ECharts CDN)
│  ├─ data.js            # 由管道生成: window.__RADAR__ = { meta, stocks[], profiles{} }
│  └─ .nojekyll
├─ pipeline/             # 自包含数据管道 (不依赖任何外部仓库)
│  ├─ datasource.py      # akshare 访问层 (UA注入 / 限频重试 / 本地缓存 / 列名匹配)
│  ├─ profile.py         # 公司深度档案 (上面 9 项的数据组装, 缺失安全)
│  ├─ watchlist.py       # 默认关注池 (改这里换成你的自选股)
│  └─ build_data.py      # 生成 docs/data.js
├─ requirements.txt
└─ README.md
```

## 更新数据

```bash
pip install -r requirements.txt

python -m pipeline.build_data              # 用默认关注池 (pipeline/watchlist.py)
python -m pipeline.build_data 600519 000858 300750   # 或只拉指定代码
```

跑完会重写 `docs/data.js`，刷新页面即可看到最新档案。想换成自己的股票池，编辑 `pipeline/watchlist.py` 再重跑。

## 数据源与代理指标说明

| 栏目 | 来源 (akshare) | 说明 |
|---|---|---|
| 公司简介 / 主营业务 | `stock_profile_cninfo`、`stock_zyjs_ths` | 巨潮机构简介、法人、官网、地址 |
| 管理层动向 | `stock_management_change_ths` | 高管/关联人近期增减持 |
| 各项业务营收占比 | `stock_zygc_em` | 主营构成: 收入占比 / 毛利率 / 分部同比 |
| 营收增速 | `stock_financial_abstract_ths` + `stock_financial_abstract` | 年度同比 + 单季营收(累计口径差分) |
| 现金流 | `stock_financial_report_sina`(现金流量表) | 经营/投资/筹资/资本开支/收购/分红, 亿元 |
| 风险 | `stock_financial_abstract_ths` | 负债率/流动比率/速动比率/毛利率/ROE + 审计 |
| 新闻(利好利空) | `stock_news_em` | 标题关键词粗分类 |
| 期权博弈(代理) | `stock_margin_detail_sse/szse` | A 股个股无期权 → 用**融资融券**多空杠杆代理 |
| 龙虎榜 | `stock_lhb_detail_em` | 近 60 日游资/机构席位上榜 |
| 暗池(代理) | `stock_dzjy_hygtj` + `stock_dzjy_mrmx` | A 股无暗池 → 用**大宗交易**场外大额成交代理 |

**已知限制**
- 同花顺(THS)接口经 `py_mini_racer`(V8) 解密，V8 引擎不能跨线程进入，故 `build_data` **单线程**执行（全市场的大宗/龙虎榜/两融文件已做进程内缓存，只下载一次，几十只关注池约几分钟）。
- 部分字段可能缺失（接口临时不可用 / 该股无相应数据），前端一律显示"暂无"，不影响其它栏目。
- 政策分析为**行业级静态梳理**，非实时，请结合最新新闻核实。

数据源：akshare（东方财富 / 同花顺 / 新浪财经 / 巨潮资讯）。前端图表：ECharts。
