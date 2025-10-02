# Cross-Exchange Hedge Bot Documentation

## 📖 Overview

Cross-Exchange Hedge Bot 是一个跨交易所对冲刷量机器人，通过在 **Paradex** 和 **Lighter** 两个交易所之间建立对冲仓位来实现高频交易刷量，同时最小化市场风险。

### 核心特性

- ✅ **跨交易所对冲**：Paradex 做多 + Lighter 做空（或反向）
- ✅ **余额检查**：启动前检查两边余额，不足则终止
- ✅ **并发下单**：使用 `asyncio.gather` 同时在两个交易所下单
- ✅ **回滚机制**：一边下单失败时自动平掉另一边
- ✅ **止盈止损**：基于单边 P&L 百分比触发
- ✅ **20秒循环**：持续开平仓，与 Lighter hedge 一致
- ✅ **支持反向模式**：可配置 Paradex SHORT + Lighter LONG

---

## 📁 新增文件

### 1. `cross_exchange_hedge_bot.py`
**功能**：跨交易所对冲机器人的核心逻辑

**主要类**：
- `CrossHedgeConfig`: 配置数据类
  - `ticker`: 交易对（如 BTC、ETH）
  - `margin`: 每次开仓保证金（USDC）
  - `hold_time`: 持仓时间（秒）
  - `take_profit`: 止盈百分比
  - `stop_loss`: 止损百分比
  - `reverse`: 是否反向（Paradex SHORT + Lighter LONG）

- `CrossPositionState`: 持仓状态跟踪
  - 记录两个交易所的订单 ID、成交价格、成交数量等

- `CrossExchangeHedgeBot`: 主要交易逻辑
  - `initialize()`: 初始化两个交易所客户端
  - `_check_account_balances()`: 检查余额
  - `_open_hedge_positions()`: 并发开仓
  - `_close_hedge_positions()`: 并发平仓
  - `_check_stop_conditions()`: 检查止盈止损条件
  - `_rollback_paradex_position()`: 回滚机制

**关键特性**：
- 支持 Paradex 和 Lighter 的独立配置
- 跨交易所平均价格计算
- 失败回滚保护

### 2. `run_cross_hedge_bot.py`
**功能**：启动脚本，负责参数解析和环境变量加载

**命令行参数**：
```bash
--ticker TEXT          # 交易对符号 (default: BTC)
--margin DECIMAL       # 每次交易保证金 USDC (default: 从 .env 读取)
--hold-time INT        # 持仓时间秒数 (default: 从 .env 读取)
--take-profit DECIMAL  # 止盈百分比 (default: 从 .env 读取)
--stop-loss DECIMAL    # 止损百分比 (default: 从 .env 读取)
--env-file TEXT        # .env 文件路径 (default: .env)
```

**环境变量校验**：
- 检查 Paradex 和 Lighter 必需的环境变量
- 缺失时提供详细的配置说明

---

## 🔧 修改的文件

### 1. `exchanges/paradex.py`

#### 新增方法：`place_market_order()`
```python
async def place_market_order(self, contract_id: str, quantity: Decimal, side: str,
                              aggressive_offset: Decimal = Decimal('0.003')) -> OrderResult
```

**功能**：使用 aggressive limit order 模拟市价单

**实现逻辑**：
1. 获取当前最佳买卖价
2. 计算 aggressive 价格：
   - 买单：`best_ask * (1 + 0.3%)`
   - 卖单：`best_bid * (1 - 0.3%)`
3. 下 post-only limit order
4. 等待最多 10 秒，检查是否成交
5. 未成交则取消订单并返回失败

**参数**：
- `contract_id`: 合约 ID
- `quantity`: 数量
- `side`: 'buy' 或 'sell'
- `aggressive_offset`: 价格偏移百分比（默认 0.3%）

**返回**：`OrderResult` 对象

---

#### 新增方法：`get_account_balance()`
```python
async def get_account_balance(self) -> Decimal
```

**功能**：获取账户可用 USDC 余额

**实现逻辑**：
1. 调用 `paradex.api_client.fetch_account()`
2. 提取 `equity` 字段（总账户价值）
3. 返回 Decimal 类型余额

**返回**：USDC 余额（Decimal）

---

### 2. `exchanges/lighter.py`

#### 新增方法：`get_account_balance()`
```python
async def get_account_balance(self) -> Decimal
```

**功能**：获取账户可用 USDC 余额

**实现逻辑**：
1. 使用官方 SDK `AccountApi` 获取账户信息
2. 提取 `available_balance` 字段
3. 除以 `1e6` 转换为 USDC（Lighter 使用 6 位小数）
4. 返回 Decimal 类型余额

**返回**：USDC 余额（Decimal）

---

### 3. `env_example.txt`

#### 新增配置项
```bash
# Cross-Exchange Hedge Bot Configuration (Paradex ↔ Lighter)
CROSS_HEDGE_MARGIN=100              # 每次交易保证金 USDC
CROSS_HEDGE_POSITION_HOLD_TIME=300  # 持仓时间秒数 (默认: 300s = 5 分钟)
CROSS_HEDGE_TAKE_PROFIT=50          # 止盈百分比 (默认: 50%)
CROSS_HEDGE_STOP_LOSS=50            # 止损百分比 (默认: 50%)
CROSS_HEDGE_REVERSE=false           # 反向模式 (false = Paradex LONG + Lighter SHORT)
```

**说明**：
- `CROSS_HEDGE_MARGIN`: 基于此计算每次开仓数量
- `CROSS_HEDGE_POSITION_HOLD_TIME`: 最长持仓时间
- `CROSS_HEDGE_TAKE_PROFIT`: 单边盈利超过此值则平仓
- `CROSS_HEDGE_STOP_LOSS`: 单边亏损超过此值则平仓
- `CROSS_HEDGE_REVERSE`:
  - `false`: Paradex 做多，Lighter 做空
  - `true`: Paradex 做空，Lighter 做多

---

## 🔄 执行逻辑

### 1. 初始化阶段

```
1. 加载环境变量和命令行参数
2. 创建 Paradex 和 Lighter 客户端
3. 连接到两个交易所
4. 获取合约信息（contract_id, tick_size）
5. 检查账户余额
   - 如果任意一边余额 < 2 × margin，终止程序
6. 等待 WebSocket 连接稳定（5秒）
```

### 2. 主交易循环（无限循环）

```
while not shutdown_requested:
    1. 开仓阶段
       ├─ 获取两个交易所的 BBO 价格
       ├─ 计算平均价格: (Paradex_mid + Lighter_mid) / 2
       ├─ 计算数量: quantity = margin / avg_price
       ├─ 并发下单:
       │  ├─ Paradex: place_market_order(quantity, 'buy' or 'sell')
       │  └─ Lighter: place_market_order(quantity, 'sell' or 'buy')
       ├─ 检查两边订单是否都成交
       └─ 失败处理:
          ├─ Paradex 失败 → 返回，等待 20秒重试
          ├─ Lighter 失败 → 立即回滚 Paradex 仓位
          └─ 部分成交 → 回滚已成交部分

    2. 持仓监控阶段
       while position_is_open:
           ├─ 检查持仓时间是否超过 hold_time
           ├─ 获取当前价格
           ├─ 计算两边 P&L:
           │  ├─ Paradex P&L % = (current_price - entry_price) / entry_price × 100
           │  └─ Lighter P&L % = (entry_price - current_price) / entry_price × 100
           ├─ 检查止盈止损:
           │  ├─ 任意一边 P&L ≤ -stop_loss% → 触发平仓
           │  └─ 任意一边 P&L ≥ take_profit% → 触发平仓
           └─ 等待 1 秒，继续监控

    3. 平仓阶段
       ├─ 并发下单:
       │  ├─ Paradex: place_market_order(quantity, 'sell' or 'buy')
       │  └─ Lighter: place_market_order(quantity, 'buy' or 'sell')
       └─ 记录平仓结果

    4. 冷却阶段
       └─ 等待 20 秒后进入下一轮
```

### 3. 关键决策点

#### 开仓失败处理
```
if Paradex 失败:
    ├─ 不下 Lighter 订单
    └─ 等待 20 秒重试

if Lighter 失败:
    ├─ 立即平掉 Paradex 仓位（市价单）
    └─ 等待 20 秒重试

if Paradex 未成交:
    ├─ 平掉 Lighter 仓位（市价单）
    └─ 等待 20 秒重试
```

#### 止盈止损触发
```
每秒检查:
if Paradex_PnL ≤ -50% or Lighter_PnL ≤ -50%:
    ├─ 触发止损
    └─ 立即平仓

if Paradex_PnL ≥ 50% or Lighter_PnL ≥ 50%:
    ├─ 触发止盈
    └─ 立即平仓
```

---

## 📋 使用方法

### 1. 环境准备

#### 安装依赖
```bash
# 激活 para_env（已包含 Lighter 和 Paradex 依赖）
source para_env/bin/activate

# 确认依赖已安装
pip list | grep -E "paradex|lighter"
```

### 2. 配置 .env 文件

创建或编辑 `.env` 文件：

```bash
# ============================================
# Paradex 配置
# ============================================
PARADEX_L1_ADDRESS=0x1234567890abcdef...           # 你的 L1 地址
PARADEX_L2_PRIVATE_KEY=0xabcdef1234567890...       # 你的 L2 私钥
PARADEX_ENVIRONMENT=prod                            # prod 或 testnet

# ============================================
# Lighter 配置
# ============================================
API_KEY_PRIVATE_KEY=0xabcdef1234567890...          # 你的 API 私钥
LIGHTER_ACCOUNT_INDEX=12345                         # 你的账户索引
LIGHTER_API_KEY_INDEX=0                             # 你的 API Key 索引

# ============================================
# 跨交易所对冲机器人配置
# ============================================
CROSS_HEDGE_MARGIN=100                              # 每次交易 100 USDC
CROSS_HEDGE_POSITION_HOLD_TIME=300                  # 持仓 5 分钟
CROSS_HEDGE_TAKE_PROFIT=50                          # 单边盈利 50% 止盈
CROSS_HEDGE_STOP_LOSS=50                            # 单边亏损 50% 止损
CROSS_HEDGE_REVERSE=false                           # false = Paradex LONG + Lighter SHORT

# ============================================
# 通知配置（可选）
# ============================================
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
LARK_TOKEN=your_lark_token

# ============================================
# 日志配置
# ============================================
ACCOUNT_NAME=my_account                             # 账户名（用于区分日志）
TIMEZONE=Asia/Shanghai
```

### 3. 启动机器人

#### 方式一：使用 .env 默认配置
```bash
# 激活虚拟环境
source para_env/bin/activate

# 启动 BTC 交易
python run_cross_hedge_bot.py --ticker BTC

# 启动 ETH 交易
python run_cross_hedge_bot.py --ticker ETH
```

#### 方式二：命令行参数覆盖
```bash
# 使用命令行参数
python run_cross_hedge_bot.py \
    --ticker BTC \
    --margin 200 \
    --hold-time 600 \
    --take-profit 30 \
    --stop-loss 40
```

#### 方式三：使用独立配置文件
```bash
# 创建多个 .env 文件
# account_1.env
# account_2.env

# 使用指定配置文件启动
python run_cross_hedge_bot.py --ticker BTC --env-file account_1.env
```

### 4. 多账号运行

使用 `tmux` 或 `screen` 管理多个会话：

```bash
# 启动第一个账号
tmux new -s cross_hedge_1
source para_env/bin/activate
python run_cross_hedge_bot.py --ticker BTC --env-file account_1.env
# 按 Ctrl+B D 分离会话

# 启动第二个账号
tmux new -s cross_hedge_2
source para_env/bin/activate
python run_cross_hedge_bot.py --ticker ETH --env-file account_2.env
# 按 Ctrl+B D 分离会话

# 查看所有会话
tmux ls

# 重新连接会话
tmux attach -t cross_hedge_1
```

---

## 📊 运行日志示例

### 启动日志
```
=== Cross-Exchange Hedge Bot Configuration ===
Ticker: BTC
Paradex Contract: BTC-USD-PERP
Lighter Contract: 1
Margin per trade: 100 USDC
Hold time: 300s
Take Profit: 50%
Stop Loss: 50%
Reverse mode: False
==============================================
```

### 开仓日志
```
=== Opening Cross-Exchange Hedge Positions ===
Prices: Paradex=96824.50, Lighter=96826.30, Avg=96825.40
Calculated quantity: 0.0010 (margin=100, avg_price=96825.40)
Normal mode: Paradex LONG + Lighter SHORT
Target: 0.0010 @ avg_price 96825.40
[MARKET] Placing buy market order: 0.0010 @ 97114.73 (bid=96534.50, ask=96824.50)
✓ Paradex LONG: 0.0010 @ 96824.50
✓ Lighter SHORT: 0.0010 @ 96826.30
=== Cross-Exchange Hedge Positions Opened Successfully ===
```

### 持仓监控日志
```
P&L: Paradex=-0.12%, Lighter=0.15%
P&L: Paradex=0.23%, Lighter=-0.18%
P&L: Paradex=0.45%, Lighter=-0.42%
Hold time expired (300s)
```

### 平仓日志
```
=== Closing Cross-Exchange Hedge Positions ===
✓ Paradex closed: 0.0010 @ 96868.20
✓ Lighter closed: 0.0010 @ 96782.50
=== Cross-Exchange Hedge Positions Closed ===
Waiting 20 seconds before next cycle...
```

---

## ⚠️ 重要注意事项

### 1. 余额要求
- **两个交易所的余额都必须 ≥ 2 × margin**
- 示例：如果 `CROSS_HEDGE_MARGIN=100`，则每个交易所至少需要 200 USDC
- 余额不足时程序会自动终止并发送通知

### 2. Paradex 市价单风险
- Paradex 使用 **aggressive limit order** 模拟市价单
- 默认价格偏移 0.3%，可能在极端行情下不立即成交
- 10秒未成交会自动取消订单并重试

### 3. 回滚延迟风险
- 一边成交、另一边失败时，回滚会有 1-3 秒延迟
- 在极端行情下可能产生小额亏损
- 建议设置较小的 `margin` 值（如 50-100 USDC）

### 4. 止盈止损逻辑
- **单边触发**：任意一边达到阈值就平仓
- 不是总 P&L，而是各自独立判断
- 默认 ±50% 可能过于宽松，建议根据实际情况调整

### 5. 网络和 API 稳定性
- 需要稳定的网络连接
- 两个交易所都需要正常的 WebSocket 连接
- 建议在 VPS 上运行以保证稳定性

### 6. 费率成本
- Paradex 和 Lighter 都有交易手续费
- 每个循环成本 = 4 × 手续费（开仓 2 次 + 平仓 2 次）
- 建议计算手续费后设置合理的 `margin` 值

---

## 🐛 故障排查

### 问题 1：余额检查失败
```
Error: Insufficient Paradex balance: 50 USDC (required: 200 USDC)
```

**解决**：
- 检查 Paradex 账户余额
- 降低 `CROSS_HEDGE_MARGIN` 值
- 充值到账户

### 问题 2：Paradex 订单不成交
```
[MARKET] Order not filled within 10s, cancelling
```

**解决**：
- 检查 Paradex 流动性
- 增加 `aggressive_offset` 值（修改代码）
- 检查网络连接

### 问题 3：Lighter WebSocket 连接失败
```
Waiting for WebSocket data... (1/10)
```

**解决**：
- 检查 Lighter API 凭证是否正确
- 检查网络连接
- 等待 WebSocket 重连（最多 20 秒）

### 问题 4：频繁触发回滚
```
Rolling back Paradex position: 0.0010 @ 96824.50
```

**解决**：
- 检查两个交易所的 API 稳定性
- 降低交易频率（增加 `hold_time`）
- 检查账户权限和 API 限流

---

## 📈 性能优化建议

### 1. 参数调优
```bash
# 低频稳定策略
CROSS_HEDGE_MARGIN=50              # 小额测试
CROSS_HEDGE_POSITION_HOLD_TIME=600 # 持仓 10 分钟
CROSS_HEDGE_TAKE_PROFIT=30         # 30% 止盈
CROSS_HEDGE_STOP_LOSS=30           # 30% 止损

# 高频激进策略
CROSS_HEDGE_MARGIN=100             # 中等金额
CROSS_HEDGE_POSITION_HOLD_TIME=180 # 持仓 3 分钟
CROSS_HEDGE_TAKE_PROFIT=50         # 50% 止盈
CROSS_HEDGE_STOP_LOSS=50           # 50% 止损
```

### 2. 多对交易
```bash
# 终端 1：BTC
python run_cross_hedge_bot.py --ticker BTC --margin 100

# 终端 2：ETH
python run_cross_hedge_bot.py --ticker ETH --margin 80

# 终端 3：SOL
python run_cross_hedge_bot.py --ticker SOL --margin 50
```

### 3. 监控脚本
创建 `monitor_hedges.sh`：
```bash
#!/bin/bash
# 监控所有对冲机器人的日志
tail -f logs/cross_hedge_BTC_*_activity.log &
tail -f logs/cross_hedge_ETH_*_activity.log &
wait
```

---

## 🔗 相关文档

- [Paradex API 文档](https://docs.paradex.trade/)
- [Lighter API 文档](https://docs.lighter.xyz/)
- [原始 Lighter Hedge Bot 文档](HEDGE_BOT_README.md)
- [Telegram 通知配置](docs/telegram-bot-setup-en.md)

---

## 📝 更新日志

### v1.0.0 (2025-01-XX)
- ✅ 初始版本
- ✅ 支持 Paradex ↔ Lighter 跨交易所对冲
- ✅ 实现余额检查和回滚机制
- ✅ 添加 Paradex `place_market_order()` 方法
- ✅ 添加两个交易所的 `get_account_balance()` 方法
- ✅ 支持反向模式（Paradex SHORT + Lighter LONG）

---

## 📧 支持

如有问题或建议，请：
1. 查看 [故障排查](#-故障排查) 章节
2. 检查日志文件 `logs/cross_hedge_<ticker>_activity.log`
3. 提交 Issue 到 GitHub 仓库

---

**免责声明**：本软件仅供学习和研究使用。加密货币交易存在重大风险，可能导致资金损失。使用本软件的风险由用户自行承担，切勿使用无法承受损失的资金进行交易。
