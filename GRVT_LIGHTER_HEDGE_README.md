# GRVT ↔ Lighter 跨交易所对冲策略

## 📋 策略概述

**与 Paradex ↔ Lighter 完全相同的对冲策略**，用于自动刷交易量。

### 核心逻辑

1. **GRVT 开仓**：POST_ONLY 限价单 (maker，低手续费)
2. **等待成交**：60秒超时
3. **Lighter 对冲**：市价单 (taker，快速成交)
4. **持仓管理**：持有指定时间或触发止盈止损
5. **GRVT 平仓**：POST_ONLY 限价单 (maker)
6. **Lighter 平仓**：市价单 (taker)
7. **循环重复**：等待指定间隔后继续

### 两种模式

- **Normal 模式** (默认)：GRVT LONG + Lighter SHORT
- **Reverse 模式**：GRVT SHORT + Lighter LONG

---

## 🚀 快速开始

### 1. 环境准备

```bash
# 确保已安装依赖
source env/bin/activate  # 激活虚拟环境
pip install -r requirements.txt

# 如果还需要安装 GRVT SDK
pip install grvt-pysdk
```

### 2. 配置环境变量

```bash
# 复制配置模板
cp grvt_lighter.env.example .env

# 编辑 .env 文件，填入你的凭证
nano .env
```

**必需配置**：

```bash
# GRVT
GRVT_TRADING_ACCOUNT_ID=your_account_id
GRVT_PRIVATE_KEY=your_private_key
GRVT_API_KEY=your_api_key

# Lighter
API_KEY_PRIVATE_KEY=your_lighter_private_key
LIGHTER_ACCOUNT_INDEX=your_account_index
LIGHTER_API_KEY_INDEX=0

# 策略参数
CROSS_HEDGE_MARGIN=100
CROSS_HEDGE_POSITION_HOLD_TIME=300
CROSS_HEDGE_TAKE_PROFIT=50
CROSS_HEDGE_STOP_LOSS=50
CROSS_HEDGE_CYCLE_WAIT=20
```

### 3. 运行策略

#### 基本用法

```bash
python run_grvt_lighter_hedge_bot.py --ticker BTC --margin 100 --hold-time 300
```

#### 使用环境变量

```bash
# margin 和 hold-time 从 .env 读取
python run_grvt_lighter_hedge_bot.py --ticker BTC
```

#### Reverse 模式

```bash
# 在 .env 中设置
CROSS_HEDGE_REVERSE=true

# 运行
python run_grvt_lighter_hedge_bot.py --ticker ETH
```

#### 多账户/多合约

```bash
# 创建多个配置文件
cp .env grvt_lighter_btc.env
cp .env grvt_lighter_eth.env

# 分别运行
python run_grvt_lighter_hedge_bot.py --env-file grvt_lighter_btc.env --ticker BTC
python run_grvt_lighter_hedge_bot.py --env-file grvt_lighter_eth.env --ticker ETH
```

---

## 📊 参数说明

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--ticker` | 交易对标的 (BTC, ETH, SOL等) | BTC |
| `--margin` | 每笔交易保证金 (USDC) | 从环境变量读取 |
| `--hold-time` | 持仓时间 (秒) | 从环境变量读取 |
| `--take-profit` | 止盈百分比 | 从环境变量读取 |
| `--stop-loss` | 止损百分比 | 从环境变量读取 |
| `--env-file` | 环境变量文件路径 | .env |

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CROSS_HEDGE_MARGIN` | 保证金 (USDC) | 100 |
| `CROSS_HEDGE_POSITION_HOLD_TIME` | 持仓时间 (秒) | 300 |
| `CROSS_HEDGE_TAKE_PROFIT` | 止盈百分比 | 50 |
| `CROSS_HEDGE_STOP_LOSS` | 止损百分比 | 50 |
| `CROSS_HEDGE_REVERSE` | 反向模式 (true/false) | false |
| `CROSS_HEDGE_CYCLE_WAIT` | 循环间隔 (秒) | 20 |

---

## 🔍 日志输出

### 日志文件位置

```
logs/
├── grvt_lighter_hedge_BTC_orders.csv         # 交易记录
├── grvt_lighter_hedge_BTC_activity.log       # 活动日志
└── grvt_lighter_hedge_BTC_ACCOUNT_orders.csv # 带账户名
```

### 关键日志示例

#### 开仓日志

```
Quantity calculation: margin=100.00 USDC, price=92543.50,
  raw_qty=0.00108032, adjusted_qty=0.001,
  actual_notional=92.54 USDC, increment=0.001

GRVT BUY target: 0.001 @ 92543.50 (bid=92543.00, ask=92544.00, avg_price=92540.25)

✓ GRVT BUY (maker): 0.001 @ 92543.50
✓ Lighter SELL (taker): 0.001 @ 92538.75

📊 Notional values - Target: 100.00 USDC |
    GRVT: 92.54 USDC (-7.46%) |
    Lighter: 92.54 USDC (-7.46%)
```

#### 警告示例

```
⚠️ Precision truncation warning: actual notional deviates 18.45% from target margin
⚠️ GRVT notional deviation: 18.45% (actual: 81.55, target: 100.00)
```

---

## ⚠️ 重要注意事项

### 1. 精度损失

**原因**：GRVT 的 `order_size_increment` 可能较大（如 BTC = 0.001），导致数量被截断。

**影响**：实际名义价值可能低于目标 margin。

**缓解**：
- 增大 `CROSS_HEDGE_MARGIN` 设置
- 选择精度更高的币种 (如 ETH, SOL)
- 监控日志中的偏离警告

### 2. GRVT 市价单实现

GRVT 没有原生市价单，我们使用 **aggressive limit order** 模拟：

```python
# 买单：卖一价 + 0.3%
price = best_ask * 1.003

# 卖单：买一价 - 0.3%
price = best_bid * 0.997
```

**优点**：成交速度快
**缺点**：滑点成本略高于真实市价单

### 3. 账户余额要求

- **GRVT**：需要足够的 USDT 保证金
- **Lighter**：需要足够的 USDC 保证金
- **杠杆**：在交易所账户设置中配置，脚本不控制

**推荐杠杆**：10x - 20x（根据风险承受能力）

### 4. 网络延迟

GRVT 开仓后，Lighter 对冲有延迟风险。如果网络慢或价格波动大，可能导致对冲成本增加。

**缓解**：
- 使用稳定的网络连接
- 选择流动性好的时间段交易
- 监控 P&L 日志

---

## 🛠️ 故障排查

### 问题 1：GRVT 连接失败

**错误**：`Failed to initialize GRVT client`

**检查**：
1. 确认 `GRVT_TRADING_ACCOUNT_ID` 正确
2. 确认 `GRVT_PRIVATE_KEY` 格式正确
3. 确认 `GRVT_API_KEY` 有效
4. 检查 `GRVT_ENVIRONMENT` 是否匹配 (prod/testnet)

### 问题 2：Lighter WebSocket 未就绪

**警告**：`Warning: Lighter WebSocket may not be fully ready`

**解决**：
- 增加等待时间（代码已设置 10 秒）
- 检查网络连接
- 重启脚本

### 问题 3：精度偏离过大

**警告**：`⚠️ Precision truncation warning: actual notional deviates 18.45%`

**原因**：币种精度太低（如 BTC = 0.001）

**解决**：
- 增大 `CROSS_HEDGE_MARGIN`（如 100 → 200）
- 换用精度更高的币种

### 问题 4：订单一直不成交

**现象**：GRVT 订单 60 秒超时被取消

**原因**：
- 价格波动太快，POST_ONLY 订单无法成交
- 盘口流动性不足

**解决**：
- 检查盘口深度
- 选择流动性更好的时间段
- 考虑调整 `tick_size` 偏移量（需修改代码）

---

## 📈 性能监控

### 关键指标

1. **成交率**：GRVT 开仓成功率
2. **对冲延迟**：GRVT 成交 → Lighter 对冲的时间
3. **名义价值偏离**：实际 notional vs 目标 margin
4. **P&L 分布**：单边盈亏情况

### 日志监控命令

```bash
# 实时查看活动日志
tail -f logs/grvt_lighter_hedge_BTC_activity.log

# 查看交易记录
cat logs/grvt_lighter_hedge_BTC_orders.csv

# 统计名义价值偏离
grep "Notional values" logs/grvt_lighter_hedge_BTC_activity.log | tail -20

# 查看警告
grep "⚠️" logs/grvt_lighter_hedge_BTC_activity.log
```

---

## 🔄 与 Paradex ↔ Lighter 的差异

| 维度 | Paradex ↔ Lighter | GRVT ↔ Lighter |
|------|-------------------|----------------|
| **文件名** | `cross_exchange_hedge_bot.py` | `grvt_lighter_hedge_bot.py` |
| **启动脚本** | `run_cross_hedge_bot.py` | `run_grvt_lighter_hedge_bot.py` |
| **日志前缀** | `cross_hedge_` | `grvt_lighter_hedge_` |
| **Maker 侧** | Paradex | GRVT |
| **Taker 侧** | Lighter | Lighter (相同) |
| **订单状态** | `CLOSED` → `FILLED` | `FILLED` |
| **市价单** | 原生 `OrderType.Market` | Aggressive limit 模拟 |
| **数量精度** | `order_size_increment` | `min_size` (映射为 `order_size_increment`) |

**核心逻辑 100% 相同**，只是交换了 maker 侧交易所。

---

## 📞 支持

如有问题，请检查：

1. **日志文件**：`logs/grvt_lighter_hedge_*_activity.log`
2. **环境变量**：确认 `.env` 配置正确
3. **余额**：确认两个交易所都有足够余额
4. **网络**：确认网络连接稳定

---

## ⚡ 快速测试命令

```bash
# 小额测试 (10 USDC, 1 分钟持仓)
python run_grvt_lighter_hedge_bot.py \
  --ticker BTC \
  --margin 10 \
  --hold-time 60 \
  --take-profit 10 \
  --stop-loss 10

# 正式运行 (100 USDC, 5 分钟持仓)
python run_grvt_lighter_hedge_bot.py \
  --ticker BTC \
  --margin 100 \
  --hold-time 300 \
  --take-profit 50 \
  --stop-loss 50
```

---

**祝交易顺利！** 🚀
