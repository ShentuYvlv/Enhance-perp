# Hedge Volume Bot - 快速开始指南

## 5 分钟快速上手

### 第 1 步：配置环境变量

编辑 `.env` 文件，添加两个 Lighter 账户的配置：

```bash
# Account 1 - 开多仓
API_KEY_PRIVATE_KEY_1=0x...your_account1_private_key...
LIGHTER_ACCOUNT_INDEX_1=0
LIGHTER_API_KEY_INDEX_1=0

# Account 2 - 开空仓
API_KEY_PRIVATE_KEY_2=0x...your_account2_private_key...
LIGHTER_ACCOUNT_INDEX_2=1
LIGHTER_API_KEY_INDEX_2=0

# 交易配置
HEDGE_MARGIN=100                    # 每笔 100 USDC 保证金
HEDGE_POSITION_HOLD_TIME=300        # 持仓 5 分钟
HEDGE_TAKE_PROFIT=50                # 止盈 50%
HEDGE_STOP_LOSS=50                  # 止损 50%
```

### 第 2 步：测试配置

运行测试脚本验证配置：

```bash
python test_hedge_bot.py
```

如果看到 "🎉 All checks passed!"，说明配置正确。

### 第 3 步：启动机器人

```bash
# BTC 对冲交易
python run_hedge_bot.py --ticker BTC

# ETH 对冲交易，保证金 200 USDC
python run_hedge_bot.py --ticker ETH --margin 200

# SOL 对冲交易，持仓 10 分钟
python run_hedge_bot.py --ticker SOL --hold-time 600
```

## 运行示例

### 正常运行日志

```
2025-10-02 18:00:00 - INFO - [HEDGE_BTC] Initializing dual Lighter accounts...
2025-10-02 18:00:02 - INFO - [HEDGE_BTC] Connecting Account 1 (LONG)...
2025-10-02 18:00:03 - INFO - [HEDGE_BTC] Connecting Account 2 (SHORT)...
2025-10-02 18:00:05 - INFO - [HEDGE_BTC] Both accounts initialized successfully
2025-10-02 18:00:05 - INFO - [HEDGE_BTC] === Hedge Volume Bot Configuration ===
2025-10-02 18:00:05 - INFO - [HEDGE_BTC] Ticker: BTC
2025-10-02 18:00:05 - INFO - [HEDGE_BTC] Margin per trade: 100 USDC
2025-10-02 18:00:05 - INFO - [HEDGE_BTC] Hold time: 300s
2025-10-02 18:00:05 - INFO - [HEDGE_BTC] Take Profit: 50%
2025-10-02 18:00:05 - INFO - [HEDGE_BTC] Stop Loss: 50%
2025-10-02 18:00:05 - INFO - [HEDGE_BTC] === Opening Hedge Positions ===
2025-10-02 18:00:06 - INFO - [HEDGE_BTC] ✓ Account 1 LONG: 0.0016 @ 62500.00
2025-10-02 18:00:06 - INFO - [HEDGE_BTC] ✓ Account 2 SHORT: 0.0016 @ 62500.00
2025-10-02 18:05:06 - INFO - [HEDGE_BTC] Hold time expired (300s)
2025-10-02 18:05:06 - INFO - [HEDGE_BTC] === Closing Hedge Positions ===
2025-10-02 18:05:07 - INFO - [HEDGE_BTC] ✓ Account 1 closed
2025-10-02 18:05:07 - INFO - [HEDGE_BTC] ✓ Account 2 closed
```

## 常见问题

### Q: 两个账户必须是不同的钱包吗？

**A**: 是的，必须使用两个完全独立的 Lighter 账户。同一个钱包下的子账户也可以。

### Q: 保证金设置多少合适？

**A**: 建议根据账户总资金的 1-5% 设置，例如账户有 10000 USDC，可以设置 100-500 USDC。

### Q: 止盈止损什么时候触发？

**A**: 机器人每秒检查一次，当任意账户的盈亏达到设定百分比时立即平仓。

### Q: 如果网络断开怎么办？

**A**: 机器人会尝试重新连接。建议在稳定网络环境下运行，或使用 VPS。

### Q: 可以同时运行多个交易对吗？

**A**: 可以，但需要为每个交易对使用不同的终端/进程运行。

## 停止机器人

按 `Ctrl+C` 停止机器人。机器人会：

1. 自动平掉所有未平仓位
2. 断开 WebSocket 连接
3. 保存日志

## 查看交易记录

日志文件位置：

```
logs/hedge_<TICKER>_orders.csv      # 订单明细
logs/hedge_<TICKER>_activity.log    # 运行日志
```

## 下一步

- 阅读完整文档：`HEDGE_BOT_README.md`
- 调整参数优化策略
- 配置 Telegram 通知接收实时警报

## 技术支持

遇到问题？

1. 运行测试脚本：`python test_hedge_bot.py`
2. 查看日志文件：`logs/hedge_*_activity.log`
3. 参考主项目文档：`README_EN.md`

---

**⚠️ 风险提示**: 此机器人用于刷量，不保证盈利。请谨慎使用，控制风险。
