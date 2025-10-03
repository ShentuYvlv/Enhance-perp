
● Lighter交易所策略分析

  一、整体架构

  Lighter交易所的实现采用了双层架构：

  1. 官方SDK层 (lighter-python)

  - 用于API调用：下单、撤单、查询订单、查询持仓
  - 使用SignerClient进行签名和身份验证
  - 使用ApiClient进行REST API查询

  2. 自定义WebSocket层 (lighter_custom_websocket.py)

  - 不使用官方SDK的WebSocket，而是自己实现
  - 原因：需要更精细的控制和错误处理
  - 订阅两个频道：
    - order_book/{market_id} - 订单簿更新
    - account_orders/{market_id}/{account_index} - 账户订单更新

  ---
  二、Lighter策略核心特点

  1. 订单状态追踪方式独特

  与其他交易所不同，Lighter使用 self.exchange_client.current_order
  来追踪当前订单状态，而不是调用 get_order_info()：

  # trading_bot.py 266-270行
  if self.config.exchange == "lighter":
      current_order_status = self.exchange_client.current_order.status
  else:
      order_info = await self.exchange_client.get_order_info(order_id)
      current_order_status = order_info.status

  原因：Lighter通过WebSocket实时更新 self.current_order，比REST API查询更快且更可靠。

  ---
  2. 下单价格策略

  # lighter.py 362-371行
  async def get_order_price(self, side: str = '') -> Decimal:
      best_bid, best_ask = await self.fetch_bbo_prices(self.config.contract_id)
      order_price = (best_bid + best_ask) / 2  # 中间价
      return order_price

  - 开仓单：使用买一卖一的中间价（mid price）
  - 平仓单：使用成交价 ± 止盈百分比

  ---
  3. 订单簿数据来源

  Lighter优先从WebSocket获取实时订单簿：

  # lighter.py 231-247行
  async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
      if (hasattr(self, 'ws_manager') and
              self.ws_manager.best_bid and self.ws_manager.best_ask):
          best_bid = Decimal(str(self.ws_manager.best_bid))
          best_ask = Decimal(str(self.ws_manager.best_ask))
      else:
          raise ValueError("WebSocket not running. No bid/ask prices available")
      return best_bid, best_ask

  关键点：如果WebSocket断开，无法获取价格，策略会报错。

  ---
  三、使用方法

  1. 环境变量配置 (.env文件)

  # Lighter配置
  API_KEY_PRIVATE_KEY=0x你的私钥  # 你的API私钥
  LIGHTER_ACCOUNT_INDEX=123456    # 账户索引
  LIGHTER_API_KEY_INDEX=0         # API密钥索引（通常是0）

  如何获取 LIGHTER_ACCOUNT_INDEX：
  1. 访问：https://mainnet.zklighter.elliot.ai/api/v1/account?by=l1_address&value=你的钱包地址
  2. 在返回的JSON中找到 account_index
  3. 如果有多个账户：
    - 短的数字 = 主账户
    - 长的数字 = 子账户

  ---
  2. 运行命令

  # 基础命令（ETH做多，0.1张合约）
  python runbot.py --exchange lighter --ticker ETH --quantity 0.1 --take-profit 0.02 --max-orders 40 --wait-time 450

  # 带网格步长控制（防止平仓单过密）
  python runbot.py --exchange lighter --ticker ETH --quantity 0.1 --take-profit 0.02
  --max-orders 40 --wait-time 450 --grid-step 0.5

  # 做空策略
  python runbot.py --exchange lighter --ticker ETH --direction sell --quantity 0.1
  --take-profit 0.02 --max-orders 40 --wait-time 450

  # 带停止价格（价格到3000时停止）
  python runbot.py --exchange lighter --ticker ETH --quantity 0.1 --take-profit 0.02
  --max-orders 40 --wait-time 450 --stop-price 3000

  ---
  四、完整交易流程

  阶段1：初始化

  1. 创建SignerClient（官方SDK）和ApiClient
  2. 获取市场配置（market_id, 精度倍数）
  3. 启动自定义WebSocket连接
  4. 订阅订单簿和账户订单更新

  阶段2：主循环（trading_bot.py）

  while True:
      1. 获取活跃订单
      2. 检查持仓是否匹配
      3. 检查是否达到止损/暂停价格
      4. 计算等待时间（基于当前订单数）
      5. 检查网格步长（防止订单过密）
      6. 下开仓单 → 等待成交 → 下平仓单

  阶段3：下开仓单（lighter.py:place_open_order）

  1. 重置 current_order 和 current_order_client_id
  2. 获取订单价格（mid price）
  3. 调用 place_limit_order() 下单
  4. 等待10秒检查订单状态：
     - 如果FILLED → 返回成功
     - 如果OPEN → 继续等待或取消重试

  阶段4：下平仓单（lighter.py:place_close_order）

  1. 计算平仓价格 = 成交价 × (1 ± take_profit%)
  2. 调用 place_limit_order() 下平仓单
  3. 等待5秒确保订单已提交
  4. 返回订单结果

  阶段5：订单取消（特殊处理）

  Lighter的订单取消有独特的等待机制：
  # trading_bot.py 288-298行
  cancel_result = await self.exchange_client.cancel_order(order_id)
  start_time = time.time()
  while (time.time() - start_time < 10 and
         self.exchange_client.current_order.status != 'CANCELED' and
         self.exchange_client.current_order.status != 'FILLED'):
      await asyncio.sleep(0.1)
  等待WebSocket更新订单状态，而不是立即相信REST API返回。

  ---
  五、关键技术细节

  1. 订单簿管理（lighter_custom_websocket.py）

  - 订单簿快照：连接时接收 subscribed/order_book 消息
  - 增量更新：接收 update/order_book 消息
  - 序列号验证：通过 offset 字段确保没有遗漏更新
  - 完整性检查：验证 best_bid < best_ask
  - 自动重连：检测到序列号跳跃或数据不一致时重新订阅

  # 订单簿更新验证逻辑
  def validate_order_book_offset(self, new_offset: int) -> bool:
      expected_offset = self.order_book_offset + 1
      if new_offset == expected_offset:
          # 正常更新
          return True
      elif new_offset > expected_offset:
          # 检测到序列号跳跃 → 请求新快照
          self.order_book_sequence_gap = True
          return False

  2. 认证Token管理

  账户订单订阅需要临时Token：
  # 10分钟过期的Token
  ten_minutes_deadline = int(time.time() + 10 * 60)
  auth_token, err = self.lighter_client.create_auth_token_with_expiry(ten_minutes_deadline)

  3. 价格和数量精度

  # 下单时需要转换为整数
  order_params = {
      'base_amount': int(quantity * self.base_amount_multiplier),  # 数量×10^精度
      'price': int(price * self.price_multiplier),                 # 价格×10^精度
  }

  ---
  六、与其他交易所的差异

  | 特性     | Lighter                   | EdgeX/Backpack/其他         |
  |--------|---------------------------|---------------------------|
  | 订单状态追踪 | WebSocket实时 current_order | REST API get_order_info() |
  | 订单簿数据  | 自定义WebSocket订阅            | 官方SDK或REST API            |
  | 订单ID生成 | client_order_index (时间戳)  | 交易所返回的order_id            |
  | 下单价格   | 中间价 (mid price)           | 略高于/低于市价                  |
  | 取消订单确认 | 等待WebSocket状态更新           | 直接相信API返回                 |
  | 订单簿维护  | 手动维护增量更新                  | SDK自动处理                   |

  ---
  七、风险和注意事项

  ⚠️ 关键风险

  1. WebSocket依赖
    - 如果WebSocket断开，无法获取价格和订单状态
    - 重连机制有指数退避（1秒→2秒→4秒→...最多30秒）
  2. 订单簿序列号跳跃
    - 网络不稳定可能导致错过订单簿更新
    - 会触发重新订阅，可能短暂无法下单
  3. 精度转换
    - 价格和数量需要乘以倍数转为整数
    - 配置错误会导致下单失败
  4. 无止损机制
    - 与所有其他交易所一样，策略不包含止损
    - 需要手动监控或设置 --stop-price

  ✅ 最佳实践

  - 使用 --grid-step 防止平仓单过于密集
  - 设置合理的 --max-orders 控制风险敞口
  - 监控日志文件确认WebSocket连接稳定
  - 测试时先用小数量（如0.01）

  ---
  八、总结

  Lighter策略的核心思路是：

  1. 通过自定义WebSocket获取实时订单簿和订单状态
  2. 以mid price下开仓限价单
  3. 成交后立即下平仓限价单（止盈价）
  4. 通过WebSocket实时追踪订单状态，而非轮询API

  这种设计使得Lighter策略的延迟更低，对市场变化反应更快，但也更依赖WebSocket连接的稳定性。
