# GRVT ↔ Lighter 跨交易所对冲刷量策略 - 完整使用文档

## 📖 目录

1. [策略简介](#策略简介)
2. [核心原理](#核心原理)
3. [安装配置](#安装配置)
4. [使用方法](#使用方法)
5. [参数说明](#参数说明)
6. [日志监控](#日志监控)
7. [常见问题](#常见问题)
8. [进阶技巧](#进阶技巧)

---

## 策略简介

### 什么是跨交易所对冲策略？

这是一个**自动化交易量生成策略**，通过在两个交易所同时开反向仓位来实现：

- **GRVT 交易所**：开仓方（maker，低手续费）
- **Lighter 交易所**：对冲方（taker，快速成交）

### 策略目标

1. ✅ **刷交易量**：持续产生交易量以获取平台奖励
2. ✅ **费率优化**：GRVT 使用 maker 订单，降低手续费成本
3. ✅ **风险对冲**：两边开反向仓位，价格风险基本对冲
4. ✅ **自动化运行**：无需人工干预，24/7 运行

### 与 Paradex ↔ Lighter 的关系

**完全相同的策略逻辑**，只是把 Paradex 换成了 GRVT：

| 项目 | Paradex ↔ Lighter | GRVT ↔ Lighter |
|------|-------------------|----------------|
| Maker 侧 | Paradex | **GRVT** |
| Taker 侧 | Lighter | Lighter |
| 核心逻辑 | ✅ 相同 | ✅ 相同 |
| 精度修复 | ✅ 已应用 | ✅ 已应用 |

---

## 核心原理

### 交易流程图

```
开始一个循环
    ↓
1. 获取 GRVT 盘口价格
    ↓
2. 计算实际下单价格（bid/ask ± tick_size）
    ↓
3. 用实际价格计算数量（margin / price）
    ↓
4. 精度截断（ROUND_DOWN）
    ↓
5. GRVT 下 POST_ONLY 限价单开仓（maker）
    ↓
6. 等待成交（最多 60 秒）
    ↓
7. Lighter 下市价单对冲（taker）
    ↓
8. 持仓监控（hold_time 或 止盈止损）
    ↓
9. GRVT 下 POST_ONLY 限价单平仓（maker）
    ↓
10. Lighter 下市价单平仓（taker）
    ↓
11. 等待 cycle_wait 秒
    ↓
回到第 1 步
```

### 两种模式详解

#### Normal 模式（默认）

```
GRVT:    做多（LONG）  → maker 订单
Lighter: 做空（SHORT） → taker 订单

价格上涨：GRVT 盈利 ✅ | Lighter 亏损 ❌ → 基本对冲
价格下跌：GRVT 亏损 ❌ | Lighter 盈利 ✅ → 基本对冲
```

#### Reverse 模式

```
GRVT:    做空（SHORT） → maker 订单
Lighter: 做多（LONG）  → taker 订单

适用场景：
- 预期价格下跌时使用
- 或纯粹为了刷量，方向无所谓
```

### 精度修复机制（重要！）

**问题**：之前 Paradex 保证金从 71 USDC 降到 38 USDC

**原因**：
1. 用两交易所平均价计算数量
2. 实际成交价不同
3. 精度截断（order_size_increment）

**修复方案**：
```python
# ❌ 旧方法：用平均价
quantity = margin / avg_price

# ✅ 新方法：用 GRVT 实际下单价
grvt_order_price = bid + tick_size  # or ask - tick_size
quantity = margin / grvt_order_price

# ✅ 精度处理：向下取整
from decimal import ROUND_DOWN
adjusted_quantity = raw_quantity.quantize(
    order_size_increment,
    rounding=ROUND_DOWN
)
```

---

## 安装配置

### 前置要求

1. **Python 版本**：3.10 - 3.12（推荐 3.11）
2. **虚拟环境**：已创建并激活
3. **依赖包**：已安装 `requirements.txt`
4. **GRVT SDK**：需额外安装

### 详细步骤

#### 1. 检查当前环境

```bash
# 查看 Python 版本
python --version
# 应该显示：Python 3.10.x 或 3.11.x 或 3.12.x

# 查看当前目录
pwd
# 应该在：/root/perp-dex-tools
```

#### 2. 激活虚拟环境

```bash
# 如果还没有虚拟环境，先创建
python3 -m venv env

# 激活虚拟环境
source env/bin/activate

# 激活后，命令行前面会显示 (env)
```

#### 3. 安装依赖

```bash
# 安装基础依赖
pip install -r requirements.txt

# 安装 GRVT SDK（必需）
pip install grvt-pysdk
```

#### 4. 验证安装

```bash
# 检查 GRVT SDK 是否安装成功
python -c "from pysdk.grvt_ccxt import GrvtCcxt; print('GRVT SDK installed successfully')"

# 应该输出：GRVT SDK installed successfully
```

---

## 使用方法

### 🚀 方法一：命令行参数（推荐新手）

#### 最简单的运行方式

```bash
python run_grvt_lighter_hedge_bot.py \
  --ticker BTC \
  --margin 100 \
  --hold-time 300
```

**参数说明**：
- `--ticker BTC`：交易 BTC 永续合约
- `--margin 100`：每笔交易使用 100 USDC 保证金
- `--hold-time 300`：持仓 300 秒（5 分钟）

#### 完整参数示例

```bash
python run_grvt_lighter_hedge_bot.py \
  --ticker BTC \
  --margin 100 \
  --hold-time 300 \
  --take-profit 50 \
  --stop-loss 50 \
  --env-file .env
```

**参数说明**：
- `--ticker BTC`：交易标的（BTC, ETH, SOL 等）
- `--margin 100`：保证金（USDC）
- `--hold-time 300`：持仓时间（秒）
- `--take-profit 50`：止盈百分比（50%）
- `--stop-loss 50`：止损百分比（50%）
- `--env-file .env`：环境变量文件路径

---

### 🔧 方法二：环境变量（推荐老手）

#### 1. 创建配置文件

```bash
# 复制模板
cp grvt_lighter.env.example my_config.env

# 编辑配置
nano my_config.env
```

#### 2. 填写配置文件

**必需配置**：

```bash
# ============ GRVT 配置 ============
GRVT_TRADING_ACCOUNT_ID=your_trading_account_id
GRVT_PRIVATE_KEY=your_private_key
GRVT_API_KEY=your_api_key
GRVT_ENVIRONMENT=prod

# ============ Lighter 配置 ============
API_KEY_PRIVATE_KEY=your_lighter_private_key
LIGHTER_ACCOUNT_INDEX=123456
LIGHTER_API_KEY_INDEX=0

# ============ 策略参数 ============
CROSS_HEDGE_MARGIN=100
CROSS_HEDGE_POSITION_HOLD_TIME=300
CROSS_HEDGE_TAKE_PROFIT=50
CROSS_HEDGE_STOP_LOSS=50
CROSS_HEDGE_REVERSE=false
CROSS_HEDGE_CYCLE_WAIT=20

# ============ 可选配置 ============
ACCOUNT_NAME=MY_ACCOUNT
TIMEZONE=Asia/Shanghai
```

#### 3. 运行策略

```bash
# 使用默认 .env
python run_grvt_lighter_hedge_bot.py --ticker BTC

# 使用自定义配置文件
python run_grvt_lighter_hedge_bot.py --ticker BTC --env-file my_config.env
```

---

### 📊 方法三：多账户/多合约（高级）

#### 场景 1：同一账户，不同合约

```bash
# 终端 1：BTC 合约
python run_grvt_lighter_hedge_bot.py --ticker BTC --margin 100

# 终端 2：ETH 合约
python run_grvt_lighter_hedge_bot.py --ticker ETH --margin 200

# 终端 3：SOL 合约
python run_grvt_lighter_hedge_bot.py --ticker SOL --margin 50
```

#### 场景 2：多个账户，不同配置

```bash
# 创建多个配置文件
cp grvt_lighter.env.example account1.env
cp grvt_lighter.env.example account2.env

# 编辑每个文件的凭证和参数
nano account1.env  # 配置账户 1
nano account2.env  # 配置账户 2

# 分别运行
python run_grvt_lighter_hedge_bot.py --env-file account1.env --ticker BTC
python run_grvt_lighter_hedge_bot.py --env-file account2.env --ticker ETH
```

#### 场景 3：使用 screen 管理多个进程

```bash
# 启动 BTC 策略
screen -S grvt_btc
python run_grvt_lighter_hedge_bot.py --ticker BTC --margin 100
# 按 Ctrl+A D 脱离

# 启动 ETH 策略
screen -S grvt_eth
python run_grvt_lighter_hedge_bot.py --ticker ETH --margin 200
# 按 Ctrl+A D 脱离

# 查看所有 screen 会话
screen -ls

# 重新连接到某个会话
screen -r grvt_btc

# 停止某个策略
screen -r grvt_btc
# 按 Ctrl+C 停止
# 输入 exit 退出 screen
```

---

### 🧪 测试模式（首次运行必看）

#### 小额测试（强烈推荐）

```bash
python run_grvt_lighter_hedge_bot.py \
  --ticker BTC \
  --margin 10 \
  --hold-time 60 \
  --take-profit 10 \
  --stop-loss 10
```

**测试目的**：
- ✅ 验证 GRVT 连接正常
- ✅ 验证 Lighter 连接正常
- ✅ 验证订单能够成交
- ✅ 验证对冲逻辑正确
- ✅ 验证平仓流程正常

**观察日志**：
```bash
# 实时查看日志
tail -f logs/grvt_lighter_hedge_BTC_activity.log
```

**测试成功的标志**：
```
✓ GRVT order filled: 0.0001 @ 92543.50
✓ Lighter order filled: 0.0001 @ 92538.75
📊 Notional values - Target: 10.00 USDC | GRVT: 9.25 USDC | Lighter: 9.25 USDC
=== Cross-Exchange Hedge Positions Opened Successfully ===
```

---

## 参数说明

### 命令行参数完整列表

| 参数 | 类型 | 必需 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--ticker` | str | 否 | BTC | 交易标的符号 |
| `--margin` | Decimal | 否 | 从环境变量 | 每笔交易保证金（USDC） |
| `--hold-time` | int | 否 | 从环境变量 | 持仓时间（秒） |
| `--take-profit` | Decimal | 否 | 从环境变量 | 止盈百分比 |
| `--stop-loss` | Decimal | 否 | 从环境变量 | 止损百分比 |
| `--env-file` | str | 否 | .env | 环境变量文件路径 |

### 环境变量完整列表

#### GRVT 配置（必需）

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `GRVT_TRADING_ACCOUNT_ID` | GRVT 交易账户 ID | `1234567890` |
| `GRVT_PRIVATE_KEY` | GRVT 私钥 | `0xabc...` |
| `GRVT_API_KEY` | GRVT API 密钥 | `abc123...` |
| `GRVT_ENVIRONMENT` | 环境 (prod/testnet) | `prod` |

#### Lighter 配置（必需）

| 变量名 | 说明 | 示例 | 获取方法 |
|--------|------|------|----------|
| `API_KEY_PRIVATE_KEY` | Lighter API 私钥 | `0x123...` | Lighter 账户设置 |
| `LIGHTER_ACCOUNT_INDEX` | Lighter 账户索引 | `123456` | 见下方说明 |
| `LIGHTER_API_KEY_INDEX` | Lighter API 密钥索引 | `0` | 通常为 0 |

**获取 LIGHTER_ACCOUNT_INDEX**：

```bash
# 在浏览器打开以下链接（替换为你的 L1 地址）
https://mainnet.zklighter.elliot.ai/api/v1/account?by=l1_address&value=YOUR_L1_ADDRESS

# 在返回的 JSON 中查找 "account_index"
# 如果有多个，短的是主账户，长的是子账户
```

#### 策略参数

| 变量名 | 说明 | 默认值 | 推荐值 |
|--------|------|--------|--------|
| `CROSS_HEDGE_MARGIN` | 保证金（USDC） | 100 | 100-200 |
| `CROSS_HEDGE_POSITION_HOLD_TIME` | 持仓时间（秒） | 300 | 300-600 |
| `CROSS_HEDGE_TAKE_PROFIT` | 止盈百分比 | 50 | 50-100 |
| `CROSS_HEDGE_STOP_LOSS` | 止损百分比 | 50 | 50-100 |
| `CROSS_HEDGE_REVERSE` | 反向模式 (true/false) | false | false |
| `CROSS_HEDGE_CYCLE_WAIT` | 循环间隔（秒） | 20 | 20-60 |

#### 可选配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `ACCOUNT_NAME` | 账户标识（用于日志区分） | 无 |
| `TIMEZONE` | 时区 | Asia/Shanghai |
| `TELEGRAM_BOT_TOKEN` | Telegram 机器人 token | 无 |
| `TELEGRAM_CHAT_ID` | Telegram 聊天 ID | 无 |
| `LARK_TOKEN` | 飞书机器人 webhook token | 无 |

### 参数优先级

```
命令行参数 > 环境变量 > 默认值
```

**示例**：
```bash
# .env 中设置
CROSS_HEDGE_MARGIN=100

# 命令行覆盖
python run_grvt_lighter_hedge_bot.py --margin 200

# 实际使用：200（命令行优先）
```

---

## 日志监控

### 日志文件位置

```
logs/
├── grvt_lighter_hedge_BTC_orders.csv          # 交易记录（CSV）
├── grvt_lighter_hedge_BTC_activity.log        # 活动日志
└── grvt_lighter_hedge_BTC_MYACCOUNT_orders.csv # 带账户名（如果设置了 ACCOUNT_NAME）
```

### 实时查看日志

```bash
# 查看活动日志（推荐）
tail -f logs/grvt_lighter_hedge_BTC_activity.log

# 查看最近 50 行
tail -n 50 logs/grvt_lighter_hedge_BTC_activity.log

# 查看交易记录
cat logs/grvt_lighter_hedge_BTC_orders.csv
```

### 日志输出示例

#### 正常开仓日志

```
2025-10-03 20:15:32 - INFO - === Opening Cross-Exchange Hedge Positions ===
2025-10-03 20:15:32 - INFO - Normal mode: GRVT LONG (maker) + Lighter SHORT (taker)
2025-10-03 20:15:32 - INFO - Quantity calculation: margin=100.00 USDC, price=92543.50, raw_qty=0.00108032, adjusted_qty=0.001, actual_notional=92.54 USDC, increment=0.001
2025-10-03 20:15:32 - INFO - GRVT BUY target: 0.001 @ 92543.50 (bid=92543.00, ask=92544.00, avg_price=92540.25)
2025-10-03 20:15:32 - INFO - Placing GRVT BUY maker order...
2025-10-03 20:15:33 - INFO - Waiting for GRVT order 123456 to fill...
2025-10-03 20:15:34 - INFO - ✓ GRVT order filled: 0.001 @ 92543.50
2025-10-03 20:15:34 - INFO - Placing Lighter SELL market order to hedge...
2025-10-03 20:15:35 - INFO - ✓ GRVT BUY (maker): 0.001 @ 92543.50
2025-10-03 20:15:35 - INFO - ✓ Lighter SELL (taker): 0.001 @ 92538.75
2025-10-03 20:15:35 - INFO - 📊 Notional values - Target: 100.00 USDC | GRVT: 92.54 USDC (-7.46%) | Lighter: 92.54 USDC (-7.46%)
2025-10-03 20:15:35 - INFO - === Cross-Exchange Hedge Positions Opened Successfully ===
```

#### 精度警告日志

```
2025-10-03 20:15:32 - WARNING - ⚠️ Precision truncation warning: actual notional deviates 18.45% from target margin
2025-10-03 20:15:35 - WARNING - ⚠️ GRVT notional deviation: 18.45% (actual: 81.55, target: 100.00)
```

#### 止盈/止损日志

```
2025-10-03 20:20:35 - INFO - P&L: GRVT=+2.50%, Lighter=-2.45%
2025-10-03 20:20:36 - INFO - Hold time expired (300s)
2025-10-03 20:20:36 - INFO - === Closing Cross-Exchange Hedge Positions ===
```

### 关键日志搜索

```bash
# 查看所有开仓
grep "Opening Cross-Exchange Hedge Positions" logs/grvt_lighter_hedge_BTC_activity.log

# 查看所有警告
grep "⚠️" logs/grvt_lighter_hedge_BTC_activity.log

# 查看名义价值偏离
grep "Notional values" logs/grvt_lighter_hedge_BTC_activity.log

# 查看止盈止损触发
grep "Stop condition met" logs/grvt_lighter_hedge_BTC_activity.log

# 统计成功开仓次数
grep "Positions Opened Successfully" logs/grvt_lighter_hedge_BTC_activity.log | wc -l
```

---

## 常见问题

### Q1: 脚本启动后立即报错

**错误信息**：
```
Error: Missing required environment variables: GRVT_TRADING_ACCOUNT_ID, GRVT_PRIVATE_KEY
```

**解决方法**：
1. 检查是否创建了 `.env` 文件
2. 检查 `.env` 文件中是否填写了所有必需变量
3. 确认使用了正确的 `--env-file` 参数

```bash
# 检查 .env 文件是否存在
ls -la .env

# 查看 .env 内容
cat .env

# 确认必需变量
grep GRVT_ .env
grep LIGHTER_ .env
```

---

### Q2: GRVT 连接失败

**错误信息**：
```
Failed to initialize GRVT client: Invalid credentials
```

**可能原因**：
1. ❌ `GRVT_TRADING_ACCOUNT_ID` 错误
2. ❌ `GRVT_PRIVATE_KEY` 格式不对
3. ❌ `GRVT_API_KEY` 无效或过期
4. ❌ `GRVT_ENVIRONMENT` 设置错误（prod vs testnet）

**解决方法**：
```bash
# 1. 登录 GRVT 网页端确认账户信息
# 2. 检查私钥格式（应该是 0x 开头的十六进制字符串）
# 3. 重新生成 API Key
# 4. 确认环境设置正确

# 测试连接（Python 交互式）
python
>>> from pysdk.grvt_ccxt import GrvtCcxt
>>> from pysdk.grvt_ccxt_env import GrvtEnv
>>> import os
>>> params = {
...     'trading_account_id': os.getenv('GRVT_TRADING_ACCOUNT_ID'),
...     'private_key': os.getenv('GRVT_PRIVATE_KEY'),
...     'api_key': os.getenv('GRVT_API_KEY')
... }
>>> client = GrvtCcxt(env=GrvtEnv.PROD, parameters=params)
>>> print("Connection successful!")
```

---

### Q3: Lighter WebSocket 未就绪

**警告信息**：
```
Warning: Lighter WebSocket may not be fully ready
```

**影响**：可能导致价格获取失败

**解决方法**：
1. **等待更长时间**：脚本已设置 10 秒等待，如果网络慢可能不够
2. **检查网络连接**：确保可以访问 Lighter API
3. **重启脚本**：有时重启可以解决

```bash
# 测试 Lighter 连接
curl https://mainnet.zklighter.elliot.ai/api/v1/health

# 应该返回：{"status":"ok"}
```

---

### Q4: 订单一直不成交

**现象**：
```
Waiting for GRVT order 123456 to fill...
GRVT order not filled within 60s, cancelling...
```

**可能原因**：
1. ❌ 价格波动太快，POST_ONLY 订单无法成交
2. ❌ 盘口流动性不足
3. ❌ 订单价格设置不合理

**解决方法**：

**方法 1：检查盘口深度**
```bash
# 手动检查 GRVT 网页端盘口
# 查看买一卖一的挂单量是否足够
```

**方法 2：选择流动性更好的时间段**
```bash
# 避开：
# - 凌晨 3-6 点（欧美休息）
# - 节假日
# 推荐：
# - 北京时间 14:00-23:00（欧美交易时段）
```

**方法 3：降低交易频率**
```bash
# 增加 cycle_wait
CROSS_HEDGE_CYCLE_WAIT=60  # 从 20 秒改为 60 秒
```

---

### Q5: 精度偏离警告

**警告信息**：
```
⚠️ Precision truncation warning: actual notional deviates 18.45% from target margin
⚠️ GRVT notional deviation: 18.45% (actual: 81.55, target: 100.00)
```

**原因**：GRVT 的 `order_size_increment` 太大，导致数量被截断

**示例**：
```
BTC order_size_increment = 0.001
目标数量：0.000782 BTC
截断后：0.0007 BTC
损失：10.5%

价格：92,000 USDC
目标保证金：100 USDC
实际保证金：64.4 USDC
偏离：35.6%
```

**解决方法**：

**方法 1：增大保证金**
```bash
# 从 100 改为 200
python run_grvt_lighter_hedge_bot.py --margin 200
```

**方法 2：换用精度更高的币种**
```bash
# BTC 精度低 (0.001) → ETH 或 SOL 精度更高
python run_grvt_lighter_hedge_bot.py --ticker ETH --margin 100
```

**方法 3：接受偏离**
```bash
# 如果偏离在 15% 以内，可以接受
# 长期运行后，偏离会平均化
```

---

### Q6: 两交易所保证金不一致

**现象**：
```
📊 Notional values - Target: 100.00 USDC |
    GRVT: 92.54 USDC (-7.46%) |
    Lighter: 98.32 USDC (-1.68%)
```

**原因**：
1. **GRVT**：精度截断导致数量减少
2. **Lighter**：市价单滑点导致成交价偏离

**是否正常**：✅ 这是正常的，只要偏离不超过 20% 就可以接受

**优化方法**：
```bash
# 增大保证金，降低精度影响
CROSS_HEDGE_MARGIN=200

# 选择流动性好的币种
--ticker ETH
```

---

### Q7: 止盈止损一直不触发

**现象**：持仓时间到期才平仓，从未触发止盈止损

**原因**：对冲仓位的 P&L 基本抵消，很难达到 ±50% 的单边阈值

**说明**：
```
GRVT LONG:   +2% 盈利
Lighter SHORT: -1.8% 亏损
净盈亏：+0.2%

止盈阈值：50%（单边）
结果：不触发
```

**这是正常的**！对冲策略的目的就是**降低价格风险**，所以很少触发止盈止损。

**如果你想更频繁触发**：
```bash
# 降低阈值
python run_grvt_lighter_hedge_bot.py --take-profit 5 --stop-loss 5
```

---

### Q8: 脚本突然停止

**可能原因**：
1. ❌ 网络中断
2. ❌ 交易所 API 维护
3. ❌ 余额不足
4. ❌ 未处理的异常

**排查方法**：
```bash
# 1. 查看最后的日志
tail -n 100 logs/grvt_lighter_hedge_BTC_activity.log

# 2. 查看是否有 ERROR
grep ERROR logs/grvt_lighter_hedge_BTC_activity.log | tail -20

# 3. 检查余额
# 登录 GRVT 和 Lighter 网页端查看余额

# 4. 测试网络连接
ping mainnet.zklighter.elliot.ai
curl https://api.grvt.io/health
```

**重启脚本**：
```bash
# 重新运行
python run_grvt_lighter_hedge_bot.py --ticker BTC --margin 100
```

---

## 进阶技巧

### 技巧 1：动态调整参数

#### 根据市场波动调整持仓时间

```bash
# 低波动市场（价格稳定）
--hold-time 600  # 10 分钟

# 高波动市场（价格剧烈波动）
--hold-time 120  # 2 分钟（快速平仓避免风险）
```

#### 根据费率调整循环间隔

```bash
# GRVT maker 返佣高峰期
CROSS_HEDGE_CYCLE_WAIT=10  # 加快频率

# 低返佣期
CROSS_HEDGE_CYCLE_WAIT=60  # 降低频率
```

---

### 技巧 2：多策略组合

#### 组合 1：Normal + Reverse 同时运行

```bash
# 终端 1：Normal 模式
python run_grvt_lighter_hedge_bot.py --ticker BTC --margin 100
# GRVT LONG + Lighter SHORT

# 终端 2：Reverse 模式（需修改 .env 或创建新配置）
# 创建 reverse.env，设置 CROSS_HEDGE_REVERSE=true
python run_grvt_lighter_hedge_bot.py --env-file reverse.env --ticker BTC --margin 100
# GRVT SHORT + Lighter LONG

# 效果：双倍交易量，完全对冲
```

#### 组合 2：多币种组合

```bash
# BTC（大资金）
python run_grvt_lighter_hedge_bot.py --ticker BTC --margin 200 &

# ETH（中资金）
python run_grvt_lighter_hedge_bot.py --ticker ETH --margin 150 &

# SOL（小资金）
python run_grvt_lighter_hedge_bot.py --ticker SOL --margin 50 &
```

---

### 技巧 3：监控脚本

#### 创建监控脚本

```bash
# 创建 monitor.sh
cat > monitor.sh << 'EOF'
#!/bin/bash
LOG_FILE="logs/grvt_lighter_hedge_BTC_activity.log"

echo "=== GRVT Lighter Hedge Bot Monitor ==="
echo ""

# 最近 10 次开仓
echo "📊 Recent Openings (Last 10):"
grep "Positions Opened Successfully" $LOG_FILE | tail -10

echo ""

# 最近 10 次平仓
echo "📊 Recent Closings (Last 10):"
grep "Positions Closed" $LOG_FILE | tail -10

echo ""

# 警告统计
echo "⚠️ Warnings:"
grep "⚠️" $LOG_FILE | wc -l
echo " warnings found"

echo ""

# 错误统计
echo "❌ Errors:"
grep "ERROR" $LOG_FILE | wc -l
echo " errors found"

echo ""

# 最新状态
echo "📡 Latest Status:"
tail -5 $LOG_FILE
EOF

# 添加执行权限
chmod +x monitor.sh

# 运行监控
./monitor.sh
```

---

### 技巧 4：自动重启

#### 使用 systemd 守护进程（Linux）

```bash
# 创建 systemd 服务文件
sudo nano /etc/systemd/system/grvt-lighter-hedge.service
```

**文件内容**：
```ini
[Unit]
Description=GRVT Lighter Hedge Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/perp-dex-tools
Environment="PATH=/root/perp-dex-tools/env/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/root/perp-dex-tools/env/bin/python /root/perp-dex-tools/run_grvt_lighter_hedge_bot.py --ticker BTC --margin 100
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**启动服务**：
```bash
# 重新加载 systemd
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start grvt-lighter-hedge

# 设置开机自启
sudo systemctl enable grvt-lighter-hedge

# 查看状态
sudo systemctl status grvt-lighter-hedge

# 查看日志
sudo journalctl -u grvt-lighter-hedge -f
```

---

### 技巧 5：性能优化

#### 减少日志输出（提高性能）

修改 `run_grvt_lighter_hedge_bot.py` 中的日志级别：

```python
# 从 WARNING 改为 ERROR
setup_logging("ERROR")
```

#### 降低 WebSocket 检查频率

修改 `grvt_lighter_hedge_bot.py` 中的等待时间：

```python
# Line 138：从 10 秒改为 5 秒
await asyncio.sleep(5)  # 原为 10
```

---

### 技巧 6：风险控制

#### 设置最大亏损退出

在 `.env` 中添加（需手动实现）：

```bash
# 最大累计亏损（USDC）
MAX_LOSS=1000

# 最大连续失败次数
MAX_CONSECUTIVE_FAILURES=5
```

#### 定时检查余额

```bash
# 创建余额检查脚本
cat > check_balance.sh << 'EOF'
#!/bin/bash
# 每小时检查一次余额
# 如果余额不足，发送通知
# TODO: 实现余额检查逻辑
EOF
```

---

## 📊 性能指标参考

### 推荐配置（保守）

```bash
CROSS_HEDGE_MARGIN=100          # 保证金
CROSS_HEDGE_POSITION_HOLD_TIME=300  # 5 分钟
CROSS_HEDGE_CYCLE_WAIT=30       # 30 秒间隔
```

**预期表现**：
- 每小时：~10 次开平仓
- 每日交易量：~500 USDC × 2（双边）= 1000 USDC
- 精度偏离：5-15%
- 网络费用：maker fee × 2 + taker fee × 2

### 推荐配置（激进）

```bash
CROSS_HEDGE_MARGIN=200
CROSS_HEDGE_POSITION_HOLD_TIME=120  # 2 分钟
CROSS_HEDGE_CYCLE_WAIT=10       # 10 秒间隔
```

**预期表现**：
- 每小时：~25 次开平仓
- 每日交易量：~2400 USDC × 2 = 4800 USDC
- 精度偏离：5-15%
- 网络费用：更高

---

## 🎓 学习资源

### 相关文档

- [Paradex ↔ Lighter 策略说明](README.md)
- [GRVT 交易所官方文档](https://docs.grvt.io/)
- [Lighter 交易所官方文档](https://docs.lighter.xyz/)

### 故障排查清单

```
□ 确认虚拟环境已激活
□ 确认所有依赖已安装
□ 确认 .env 文件存在且配置正确
□ 确认 GRVT 和 Lighter 账户有足够余额
□ 确认网络连接正常
□ 确认日志文件正常生成
□ 确认没有其他实例在运行相同配置
```

---

## 📞 获取帮助

如果遇到问题：

1. **查看日志**：`tail -f logs/grvt_lighter_hedge_*_activity.log`
2. **检查余额**：登录 GRVT 和 Lighter 网页端
3. **验证配置**：`cat .env | grep -v '^#'`
4. **测试连接**：小额测试运行

---

**祝交易顺利！** 🚀

*最后更新：2025-10-03*
