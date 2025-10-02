# Hedge Volume Bot - 对冲刷量机器人

## 概述

`HedgeVolumeBot` 是一个双账户高频对冲交易机器人，专门用于在 Lighter 交易所刷交易量。该机器人使用两个独立的 Lighter 账户同时开立等量的多空仓位，实现风险对冲，纯粹为了产生交易量。

## 核心特性

- ✅ **双账户对冲**: 使用两个独立账户同时开多空仓
- ✅ **市价单执行**: 所有订单使用市价单立即成交
- ✅ **止盈止损**: 支持百分比止盈止损（默认 50%）
- ✅ **持续循环**: 开仓 → 等待/监控 → 平仓 → 立即再开仓
- ✅ **自动回滚**: 如果一个账户失败，自动回滚另一个账户
- ✅ **实时监控**: 持续监控 P&L 并自动触发止盈/止损

## 交易逻辑

```
1. Account 1 开多单 (LONG)
   Account 2 开空单 (SHORT)
   ↓
2. 持仓监控
   - 每秒检查止盈/止损条件
   - 达到持仓时间或触发止盈/止损
   ↓
3. Account 1 平多单 (市价卖出)
   Account 2 平空单 (市价买入)
   ↓
4. 等待 5 秒后重新开始循环
```

## 安装和配置

### 1. 环境变量配置

在 `.env` 文件中添加以下配置：

```bash
# Lighter 双账户配置
# Account 1 - 开多仓
API_KEY_PRIVATE_KEY_1=your_account1_private_key_here
LIGHTER_ACCOUNT_INDEX_1=0
LIGHTER_API_KEY_INDEX_1=0

# Account 2 - 开空仓
API_KEY_PRIVATE_KEY_2=your_account2_private_key_here
LIGHTER_ACCOUNT_INDEX_2=1
LIGHTER_API_KEY_INDEX_2=0

# 对冲机器人配置
HEDGE_MARGIN=100                    # 每笔交易保证金 (USDC)
HEDGE_POSITION_HOLD_TIME=300        # 持仓时间（秒），默认 300 秒 = 5 分钟
HEDGE_TAKE_PROFIT=50                # 止盈百分比，默认 50%
HEDGE_STOP_LOSS=50                  # 止损百分比，默认 50%

# 通知配置（可选）
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
LARK_TOKEN=your_lark_token
```

### 2. 获取 Lighter 账户配置

参考主 README 中的 Lighter 配置说明：

```bash
# 查找你的账户索引
https://mainnet.zklighter.elliot.ai/api/v1/account?by=l1_address&value=YOUR_WALLET_ADDRESS
```

## 使用方法

### 基本用法

```bash
# 使用默认配置（从 .env 读取所有参数）
python run_hedge_bot.py --ticker BTC

# 指定自定义参数
python run_hedge_bot.py --ticker ETH --margin 200 --hold-time 600
```

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--ticker` | 交易品种（BTC, ETH, SOL 等） | BTC |
| `--margin` | 每笔交易保证金（USDC） | 从环境变量 `HEDGE_MARGIN` 读取 |
| `--hold-time` | 持仓时间（秒） | 从环境变量 `HEDGE_POSITION_HOLD_TIME` 读取 |
| `--take-profit` | 止盈百分比 | 从环境变量 `HEDGE_TAKE_PROFIT` 读取（默认 50） |
| `--stop-loss` | 止损百分比 | 从环境变量 `HEDGE_STOP_LOSS` 读取（默认 50） |
| `--env-file` | 环境变量文件路径 | .env |

### 示例

```bash
# BTC 对冲交易，保证金 100 USDC，持仓 5 分钟
python run_hedge_bot.py --ticker BTC --margin 100 --hold-time 300

# ETH 对冲交易，保证金 200 USDC，持仓 10 分钟，止盈/止损 30%
python run_hedge_bot.py --ticker ETH --margin 200 --hold-time 600 --take-profit 30 --stop-loss 30

# SOL 对冲交易，使用自定义 env 文件
python run_hedge_bot.py --ticker SOL --env-file hedge_config.env
```

## 工作原理

### 开仓逻辑

1. 获取当前市场最佳买卖价（bid/ask）
2. 根据保证金和当前价格计算开仓数量：`quantity = margin / mid_price`
3. **并发执行**两个账户的开仓：
   - Account 1: 市价买入（LONG）
   - Account 2: 市价卖出（SHORT）
4. 如果任一账户失败，立即回滚成功的账户

### 市价单实现

由于 Lighter 不支持原生市价单，机器人使用**激进限价单**模拟市价单：

- **买入**: 使用 `ask_price * (1 + 0.2%)` 作为限价
- **卖出**: 使用 `bid_price * (1 - 0.2%)` 作为限价

这确保订单能够立即成交，模拟市价单效果。

### 止盈止损监控

每秒检查一次：

```python
# Account 1 (LONG) P&L
pnl_1 = (current_price - entry_price) / entry_price * 100

# Account 2 (SHORT) P&L
pnl_2 = (entry_price - current_price) / entry_price * 100

# 触发条件
if pnl_1 >= take_profit or pnl_1 <= -stop_loss:
    close_positions()

if pnl_2 >= take_profit or pnl_2 <= -stop_loss:
    close_positions()
```

### 平仓逻辑

1. **并发执行**两个账户的平仓：
   - Account 1: 市价卖出（平多）
   - Account 2: 市价买入（平空）
2. 记录结果并重置仓位状态
3. 等待 5 秒后开始下一轮循环

## 日志和监控

### 日志文件

机器人会生成以下日志文件：

- `logs/hedge_<ticker>_orders.csv` - 订单记录（CSV 格式）
- `logs/hedge_<ticker>_activity.log` - 活动日志

### 日志示例

```
2025-10-02 18:00:00 - INFO - [HEDGE_BTC] === Opening Hedge Positions ===
2025-10-02 18:00:00 - INFO - [HEDGE_BTC] Target: 0.0050 @ mid_price 62500.00
2025-10-02 18:00:01 - INFO - [HEDGE_BTC] ✓ Account 1 LONG: 0.0050 @ 62520.00
2025-10-02 18:00:01 - INFO - [HEDGE_BTC] ✓ Account 2 SHORT: 0.0050 @ 62480.00
2025-10-02 18:00:01 - INFO - [HEDGE_BTC] === Hedge Positions Opened Successfully ===
2025-10-02 18:05:00 - INFO - [HEDGE_BTC] P&L: Account1=2.5%, Account2=2.3%
2025-10-02 18:05:01 - INFO - [HEDGE_BTC] Hold time expired (300s)
2025-10-02 18:05:01 - INFO - [HEDGE_BTC] === Closing Hedge Positions ===
2025-10-02 18:05:02 - INFO - [HEDGE_BTC] ✓ Account 1 closed: 0.0050 @ 62600.00
2025-10-02 18:05:02 - INFO - [HEDGE_BTC] ✓ Account 2 closed: 0.0050 @ 62550.00
```

## 风险警告

⚠️ **重要提示**：

1. **不追求利润**: 该机器人设计目的是刷交易量，不是盈利工具
2. **忽略手续费**: 频繁交易会产生大量手续费
3. **滑点风险**: 市价单可能有滑点，导致两个账户开仓价格不完全一致
4. **资金费率**: 如果持仓时间跨越资金费率结算时间，需要支付资金费率
5. **网络延迟**: 极端情况下可能导致一个账户成交另一个不成交
6. **止盈止损触发**: 市场剧烈波动时可能提前触发止盈/止损

## 错误处理

### 开仓失败

如果一个账户开仓成功而另一个失败：

1. 机器人会自动回滚成功的账户
2. 记录错误日志
3. 等待 10 秒后重试

### 平仓失败

如果平仓失败：

1. 记录错误日志
2. 发送通知（如果配置了 Telegram/Lark）
3. 需要手动处理残留仓位

### 网络异常

机器人会在 WebSocket 连接初始化后等待 5 秒，确保连接稳定。如果遇到网络问题，建议：

1. 检查网络连接
2. 检查 Lighter API 服务状态
3. 查看日志文件定位问题

## 故障排查

### 问题：无法初始化账户

**可能原因**：
- 环境变量配置错误
- API 密钥无效
- 账户索引错误

**解决方法**：
1. 检查 `.env` 文件中的配置
2. 确认两个账户的私钥和索引都正确
3. 使用 Lighter API 验证账户信息

### 问题：订单未成交

**可能原因**：
- 市场流动性不足
- 价格偏移量设置过小

**解决方法**：
1. 检查市场深度
2. 增加 `aggressive_offset` 参数（需要修改代码）
3. 选择流动性更好的交易对

### 问题：止盈止损频繁触发

**可能原因**：
- 止盈止损百分比设置过小
- 市场波动剧烈

**解决方法**：
1. 增加 `--take-profit` 和 `--stop-loss` 参数值
2. 选择波动性较小的市场时段

## 高级配置

### 修改市价单激进度

编辑 `exchanges/lighter.py:362` 中的 `aggressive_offset` 参数：

```python
async def place_market_order(self, contract_id: str, quantity: Decimal, side: str,
                              aggressive_offset: Decimal = Decimal('0.005')):  # 改为 0.5%
```

### 调整监控频率

编辑 `hedge_volume_bot.py` 中的监控循环：

```python
# 从每秒检查改为每 0.5 秒检查
await asyncio.sleep(0.5)  # 原来是 1
```

## 性能优化建议

1. **持仓时间**: 建议设置 300-600 秒（5-10 分钟），避免过于频繁
2. **保证金**: 根据账户资金量和风险承受能力设置
3. **止盈止损**: 建议设置 30%-100%，避免市场小幅波动就触发
4. **交易品种**: 选择流动性好的品种（BTC, ETH）

## 许可和免责声明

本软件仅供学习和研究使用。加密货币交易涉及重大风险，可能导致重大财务损失。使用风险自负，切勿用您无法承受损失的资金进行交易。

---

**开发者**: 基于 perp-dex-tools 项目扩展
**联系方式**: 参考主项目 README
