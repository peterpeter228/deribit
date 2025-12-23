# Deribit Options Analytics MCP Server

🚀 **为 BTC/ETH 期权提供"可交易决策级"聚合指标的 MCP Server**

专为量化交易系统设计，提供高质量、低 token 消耗的期权分析数据，支持上层策略在 SWING/SCALP 模式间自动切换。

## ✨ 特性

### 核心分析能力

- **期权链分析** (`get_option_chain`): 完整期权链数据，含 Greeks、OI、Volume
- **OI 分布分析** (`get_open_interest_by_strike`): 按 strike 聚合 OI，识别关键支撑/阻力位
- **Gamma Exposure** (`compute_gamma_exposure`): 计算 GEX Profile、Gamma Flip Level
- **Max Pain** (`compute_max_pain`): 计算最大痛苦点，预测到期日价格吸引区
- **IV Term Structure** (`get_iv_term_structure`): ATM IV 期限结构 + 斜率分析
- **Skew Metrics** (`get_skew_metrics`): RR25d、BF25d 偏斜指标 + 趋势分析

### 工程特性

- **极简输出**: 每个 tool 返回 ≤2KB 紧凑 JSON（硬目标 5KB）
- **智能缓存**: 双层 TTL 缓存（快速 1s / 慢速 30s）
- **限速保护**: Token Bucket 限速器，避免触发 API 限制
- **可解释错误**: `error_code` + `message` + `retry_after_ms`
- **双模式部署**: 支持 stdio（本地）和 HTTP/SSE（远程）

## 📦 安装

### 使用 uv（推荐）

```bash
# 克隆项目
git clone https://github.com/your-repo/deribit-mcp-server.git
cd deribit-mcp-server

# 安装依赖
uv sync

# 或者安装开发依赖
uv sync --dev
```

### 使用 pip

```bash
pip install -e .

# 安装开发依赖
pip install -e ".[dev]"
```

## ⚙️ 配置

### 环境变量

创建 `.env` 文件或设置环境变量：

```bash
# 环境选择（prod/test），默认 prod
DERIBIT_ENV=prod

# Private API 开关，默认 false（只读模式）
DERIBIT_ENABLE_PRIVATE=false

# API 凭证（仅 Private API 需要）
DERIBIT_CLIENT_ID=YOUR_CLIENT_ID
DERIBIT_CLIENT_SECRET=YOUR_CLIENT_SECRET

# 网络设置
DERIBIT_TIMEOUT_S=10
DERIBIT_MAX_RPS=8

# 缓存 TTL（秒）
DERIBIT_CACHE_TTL_FAST=1.0   # ticker/orderbook
DERIBIT_CACHE_TTL_SLOW=30.0  # instruments/expirations

# 交易安全（默认 true = 只模拟不执行）
DERIBIT_DRY_RUN=true

# HTTP 服务器设置
DERIBIT_HOST=0.0.0.0
DERIBIT_PORT=8000
```

## 🚀 启动服务器

### 方式 1: stdio 模式（本地 MCP 客户端）

```bash
# 使用 uv
uv run deribit-mcp

# 或直接运行
python -m deribit_mcp.server
```

### 方式 2: HTTP/SSE 模式（远程部署）

```bash
# 使用 uv
uv run deribit-mcp-http

# 或直接运行
python -m deribit_mcp.http_server

# 自定义端口
DERIBIT_PORT=9000 python -m deribit_mcp.http_server
```

HTTP 服务器端点：
- `GET /health` - 健康检查
- `GET /tools` - 列出所有工具
- `POST /tools/call` - 调用工具
- `GET /sse` 或 `GET /mcp/sse` - SSE 连接（MCP 协议）
- `POST /messages` 或 `POST /mcp/messages` - MCP 消息

## 🔧 MCP 客户端配置

### Cursor 配置

在 Cursor Settings → MCP 中添加：

```json
{
  "mcpServers": {
    "deribit": {
      "command": "uv",
      "args": ["run", "deribit-mcp"],
      "cwd": "/path/to/deribit-mcp-server",
      "env": {
        "DERIBIT_ENV": "prod"
      }
    }
  }
}
```

### CherryStudio / HTTP 远程连接

```json
{
  "mcpServers": {
    "deribit": {
      "transport": "sse",
      "url": "http://your-server:8000/sse"
    }
  }
}
```

---

## 🛠️ Options Analytics Tools（新增）

### 1. `get_option_chain`

获取指定到期日的期权链数据。

**输入 Schema:**
```json
{
  "currency": "BTC",      // 必填: "BTC" 或 "ETH"
  "expiry": "28JUN24"     // 必填: 到期日标签
}
```

**输出示例:**
```json
{
  "ccy": "BTC",
  "expiry": "28JUN24",
  "expiry_ts": 1719561600000,
  "spot": 67500.0,
  "atm_strike": 67000,
  "days_to_expiry": 30.5,
  "strikes": [
    {
      "strike": 65000,
      "type": "call",
      "mark_iv": 0.72,        // IV (decimal, 0.72 = 72%)
      "delta": 0.65,
      "gamma": 0.00001,
      "vega": 120.5,
      "oi": 1500.0,           // Open Interest (contracts)
      "vol": 250.0            // 24h Volume
    },
    {
      "strike": 65000,
      "type": "put",
      "mark_iv": 0.78,
      "delta": -0.35,
      "gamma": 0.00001,
      "vega": 120.5,
      "oi": 2200.0,
      "vol": 180.0
    }
  ],
  "summary": {
    "total_oi": 45000,
    "total_volume": 8500,
    "avg_iv": 0.75,
    "num_strikes": 21
  },
  "notes": ["strikes_limited:21_of_85"]
}
```

**用途:** 获取完整期权链视图，用于分析 Greeks 分布和 OI 热点。

---

### 2. `get_open_interest_by_strike`

获取按 strike 聚合的 OI 分布。

**输入 Schema:**
```json
{
  "currency": "BTC",      // 必填
  "expiry": "28JUN24"     // 必填
}
```

**输出示例:**
```json
{
  "ccy": "BTC",
  "expiry": "28JUN24",
  "spot": 67500.0,
  "total_call_oi": 85000.0,
  "total_put_oi": 72000.0,
  "pcr_total": 0.847,         // Put/Call Ratio
  "oi_by_strike": [
    {"strike": 60000, "call_oi": 5000, "put_oi": 12000, "total_oi": 17000, "pcr": 2.4},
    {"strike": 65000, "call_oi": 8000, "put_oi": 9500, "total_oi": 17500, "pcr": 1.19},
    {"strike": 70000, "call_oi": 15000, "put_oi": 8000, "total_oi": 23000, "pcr": 0.53}
  ],
  "top_strikes": [
    {"strike": 70000, "call_oi": 15000, "put_oi": 8000, "total_oi": 23000, "pcr": 0.53},
    {"strike": 65000, "call_oi": 8000, "put_oi": 9500, "total_oi": 17500, "pcr": 1.19}
  ],
  "peak_range": {
    "low": 62000,
    "high": 72000,
    "concentration": 0.78       // 78% of OI in this range
  },
  "notes": []
}
```

**用途:** 识别 OI 峰值区域（潜在支撑/阻力），分析市场定位。

---

### 3. `compute_gamma_exposure`

计算 Gamma Exposure (GEX) Profile。

**输入 Schema:**
```json
{
  "currency": "BTC",                        // 必填
  "expiries": ["28JUN24", "27DEC24"]        // 可选: 不填则使用最近 3 个到期日
}
```

**输出示例:**
```json
{
  "ccy": "BTC",
  "spot": 67500.0,
  "expiries_included": ["28JUN24", "27DEC24"],
  "net_gex": 2.35,                          // 总净 GEX (M$)
  "gamma_flip": 65800.0,                    // Gamma Flip Level
  "max_pos_gex_strike": 68000,              // 最大正 GEX 的 strike
  "max_neg_gex_strike": 72000,              // 最大负 GEX 的 strike
  "gex_by_strike": [
    {"strike": 64000, "call_gex": -0.8, "put_gex": 1.2, "net_gex": 0.4},
    {"strike": 66000, "call_gex": -1.5, "put_gex": 1.8, "net_gex": 0.3},
    {"strike": 68000, "call_gex": -2.0, "put_gex": 2.8, "net_gex": 0.8},
    {"strike": 70000, "call_gex": -2.5, "put_gex": 1.5, "net_gex": -1.0}
  ],
  "top_positive": [
    {"strike": 68000, "call_gex": -2.0, "put_gex": 2.8, "net_gex": 0.8}
  ],
  "top_negative": [
    {"strike": 70000, "call_gex": -2.5, "put_gex": 1.5, "net_gex": -1.0}
  ],
  "market_maker_positioning": "long_gamma",   // "long_gamma" | "short_gamma" | "neutral"
  "notes": ["expiries:2"]
}
```

**GEX 解读:**
- **正 GEX (MM Long Gamma)**: MM 在上涨时卖出、下跌时买入 → **稳定市场**
- **负 GEX (MM Short Gamma)**: MM 在上涨时追买、下跌时追卖 → **放大波动**
- **Gamma Flip Level**: Net GEX 从正变负的价格点，是关键转折位

**用途:** 判断市场稳定性，识别波动放大区域，优化入场时机。

---

### 4. `compute_max_pain`

计算期权最大痛苦点。

**输入 Schema:**
```json
{
  "currency": "BTC",      // 必填
  "expiry": "28JUN24"     // 必填
}
```

**输出示例:**
```json
{
  "ccy": "BTC",
  "expiry": "28JUN24",
  "expiry_ts": 1719561600000,
  "spot": 67500.0,
  "max_pain_strike": 65000,
  "distance_from_spot_pct": -3.70,          // (max_pain - spot) / spot * 100
  "pain_curve_top3": [
    {"strike": 65000, "pain": 12500000},    // 最低 pain
    {"strike": 64000, "pain": 14800000},
    {"strike": 66000, "pain": 15200000}
  ],
  "total_call_oi": 85000.0,
  "total_put_oi": 72000.0,
  "pcr": 0.847,
  "notes": []
}
```

**Max Pain 理论:** 到期时价格倾向于向 Max Pain Strike 移动，使期权买方损失最大化。

**用途:** 预测到期日价格吸引区，辅助到期周策略。

---

### 5. `get_iv_term_structure`

获取 ATM IV 期限结构。

**输入 Schema:**
```json
{
  "currency": "BTC",                        // 必填
  "tenors_days": [7, 14, 30, 60, 90]        // 可选: 默认 [7, 14, 30, 60, 90]
}
```

**输出示例:**
```json
{
  "ccy": "BTC",
  "spot": 67500.0,
  "term_structure": [
    {"days": 7, "expiry": "05JAN24", "atm_iv": 0.85, "atm_iv_pct": 85.0},
    {"days": 14, "expiry": "12JAN24", "atm_iv": 0.78, "atm_iv_pct": 78.0},
    {"days": 30, "expiry": "28JAN24", "atm_iv": 0.72, "atm_iv_pct": 72.0},
    {"days": 60, "expiry": "28FEB24", "atm_iv": 0.68, "atm_iv_pct": 68.0},
    {"days": 90, "expiry": "28MAR24", "atm_iv": 0.65, "atm_iv_pct": 65.0}
  ],
  "slope_7d_30d": -3.25,                    // IV% change per 30 days
  "slope_30d_90d": -1.75,
  "shape": "backwardation",                 // "contango" | "backwardation" | "flat"
  "dvol_current": 75.5,
  "notes": []
}
```

**期限结构解读:**
- **Backwardation**: 短期 IV > 长期 IV → 近期有事件风险
- **Contango**: 短期 IV < 长期 IV → 市场平静，远期不确定性
- **Slope**: 斜率越陡，期限结构越扭曲

**用途:** 选择最佳期权到期日，识别 IV 定价机会。

---

### 6. `get_skew_metrics`

获取波动率偏斜指标。

**输入 Schema:**
```json
{
  "currency": "BTC",              // 必填
  "tenors_days": [7, 30]          // 可选: 默认 [7, 30]
}
```

**输出示例:**
```json
{
  "ccy": "BTC",
  "spot": 67500.0,
  "skew_by_tenor": [
    {
      "days": 7,
      "expiry": "05JAN24",
      "atm_iv": 0.85,
      "rr25d": -0.035,            // RR25d (decimal): Call_IV - Put_IV
      "rr25d_pct": -3.5,          // RR25d (%)
      "bf25d": 0.018,             // BF25d (decimal): Wing premium
      "bf25d_pct": 1.8,           // BF25d (%)
      "skew_dir": "bearish"       // "bullish" | "bearish" | "neutral"
    },
    {
      "days": 30,
      "expiry": "28JAN24",
      "atm_iv": 0.72,
      "rr25d": -0.025,
      "rr25d_pct": -2.5,
      "bf25d": 0.012,
      "bf25d_pct": 1.2,
      "skew_dir": "bearish"
    }
  ],
  "skew_trend": "steepening",     // "steepening" | "flattening" | "stable"
  "summary": {
    "avg_rr25d_pct": -3.0,
    "avg_bf25d_pct": 1.5,
    "dominant_direction": "bearish",
    "tenors_analyzed": 2
  },
  "notes": []
}
```

**Skew 解读:**
- **RR25d < 0 (Bearish)**: Put 比 Call 贵 → 下行保护需求
- **RR25d > 0 (Bullish)**: Call 比 Put 贵 → 上行投机需求
- **BF25d > 0**: Wings 比 ATM 贵 → 尾部风险定价高
- **Steepening**: 短期 skew 比长期更极端 → 近期情绪紧张

**用途:** 判断市场情绪，选择策略方向（看涨/看跌），优化限价单位置。

---

## 🛠️ 基础 Public Tools

### `deribit_status`
检查 API 连通性和状态。

### `deribit_instruments`
获取可用合约列表（最多 50 个）。

### `deribit_ticker`
获取紧凑的市场快照。

### `deribit_orderbook_summary`
获取订单簿摘要（仅 top 5 档）。

### `dvol_snapshot`
获取 DVOL（Deribit 波动率指数）快照。

### `options_surface_snapshot`
获取波动率曲面快照（ATM IV、RR、BF）。

### `expected_move_iv`
基于 IV 计算预期波动（1σ）。

### `funding_snapshot`
获取永续合约资金费率快照。

---

## 🔒 Private Tools

需要 `DERIBIT_ENABLE_PRIVATE=true` 和有效 API 凭证。

- `account_summary` - 账户摘要
- `positions` - 持仓列表
- `open_orders` - 挂单列表
- `place_order` - 下单（默认 DRY_RUN）
- `cancel_order` - 取消订单

---

## 📊 数值单位规范

| 指标 | 单位 | 示例 |
|------|------|------|
| IV (mark_iv, atm_iv) | 小数 (decimal) | `0.80` = 80% |
| IV (atm_iv_pct) | 百分比 (%) | `80.0` = 80% |
| RR25d, BF25d | 小数 | `0.025` = 2.5% |
| RR25d_pct, BF25d_pct | 百分比 | `2.5` = 2.5% |
| GEX | 百万美元 (M$) | `1.5` = 150万美元 |
| Pain | 美元 ($) | `12500000` = $12.5M |
| Slope | IV% 变化/30天 | `-3.25` = 30天内 IV 下降 3.25% |
| Distance | 百分比 (%) | `-3.7` = 比现价低 3.7% |

---

## 🧪 测试

```bash
# 运行所有测试
python3 -m pytest

# 运行测试并显示覆盖率
python3 -m pytest --cov=deribit_mcp --cov-report=term-missing

# 运行特定测试
python3 -m pytest tests/test_analytics.py -v
```

---

## 📁 项目结构

```
deribit-mcp-server/
├── pyproject.toml          # 项目配置
├── README.md               # 文档
├── .env.example            # 环境变量示例
├── src/
│   └── deribit_mcp/
│       ├── __init__.py     # 包初始化
│       ├── config.py       # 配置管理
│       ├── client.py       # JSON-RPC 客户端（缓存/限速/重试）
│       ├── models.py       # Pydantic 数据模型
│       ├── analytics.py    # 分析计算（GEX/MaxPain/Skew）
│       ├── tools.py        # MCP Tools 实现
│       ├── server.py       # stdio MCP 服务器
│       └── http_server.py  # HTTP/SSE 服务器
└── tests/
    ├── conftest.py         # 测试配置
    ├── test_analytics.py   # 分析模块测试
    ├── test_client.py      # 客户端测试
    └── test_tools.py       # Tools 测试
```

---

## 🔄 部署

### Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .

RUN pip install uv && uv sync

ENV DERIBIT_ENV=prod
ENV DERIBIT_HOST=0.0.0.0
ENV DERIBIT_PORT=8000

EXPOSE 8000

CMD ["uv", "run", "deribit-mcp-http"]
```

```bash
docker build -t deribit-mcp .
docker run -p 8000:8000 \
  -e DERIBIT_CLIENT_ID=YOUR_CLIENT_ID \
  -e DERIBIT_CLIENT_SECRET=YOUR_CLIENT_SECRET \
  deribit-mcp
```

### systemd 服务

参见 `scripts/install.sh` 获取完整的 systemd 服务安装脚本。

---

## 📊 性能目标

| 指标 | 目标值 |
|------|--------|
| 单个 Tool 输出大小 | ≤2KB（软目标），≤5KB（硬限制） |
| ticker 响应时间 | <200ms（含缓存） |
| 缓存命中率 | >80%（正常使用） |
| API 请求限速 | 8 RPS（可配置） |

---

## ⚠️ 错误处理

所有 Tool 返回统一错误格式：

```json
{
  "error": true,
  "error_code": 10001,
  "message": "Error description (max 100 chars)",
  "retry_after_ms": 5000,
  "notes": ["context_info"]
}
```

常见错误码：
- `-1`: 内部错误
- `404`: 未找到（如无效到期日）
- `10028`: 请求过快（Rate Limit）
- `13009`: 认证错误

---

## 📄 许可证

MIT License

## ⚠️ 免责声明

本项目仅供学习和研究目的。使用本软件进行交易的风险由用户自行承担。请确保了解并遵守 Deribit 的服务条款和相关法规。
