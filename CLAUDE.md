# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a modular trading bot for perpetual futures DEXs (decentralized exchanges). It implements an automated market-making strategy that places limit orders near market price and automatically closes them at a small profit target. The bot supports multiple exchanges: EdgeX, Backpack, Paradex, Aster, and Lighter.

**Key Strategy**: The bot continuously places maker orders, waits for fills, then places take-profit orders at a small spread (typically 0.02%). It's designed for high-volume trading with minimal wear and tear.

## Development Setup

### Environment Setup

```bash
# Create and activate virtual environment
python3 -m venv env
source env/bin/activate  # Windows: env\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# For Paradex exchange only, use separate environment
python3 -m venv para_env
source para_env/bin/activate
pip install -r para_requirements.txt
```

### Configuration

Create a `.env` file from `env_example.txt`. Required environment variables depend on the exchange being used. See README_EN.md for complete list.

### Running the Bot

```bash
python runbot.py --exchange edgex --ticker ETH --quantity 0.1 --take-profit 0.02 --max-orders 40 --wait-time 450
```

Key parameters:
- `--exchange`: Exchange to use (edgex, backpack, paradex, aster, lighter)
- `--ticker`: Base asset symbol (ETH, BTC, SOL, etc.)
- `--quantity`: Order size
- `--take-profit`: Profit target in percentage points (0.02 = 0.02%)
- `--max-orders`: Maximum concurrent close orders
- `--wait-time`: Cooldown between placing new orders (seconds)
- `--grid-step`: Minimum price distance between close orders (percentage)
- `--stop-price`: Exit when price reaches this level
- `--pause-price`: Pause trading when price reaches this level
- `--aster-boost`: Enable Boost mode (Aster only)

### Testing

```bash
# Activate virtual environment first
source env/bin/activate  # Windows: env\Scripts\activate

# Run specific test
python -m pytest tests/test_query_retry.py

# Run all tests
python -m pytest tests/
```

## Architecture

### Core Components

1. **runbot.py** - Entry point, parses CLI arguments and initializes TradingBot
2. **trading_bot.py** - Main trading logic (`TradingBot` class)
3. **exchanges/** - Exchange client implementations
4. **helpers/** - Logging, notifications (Telegram/Lark)

### Exchange Client Architecture

The bot uses a factory pattern with lazy loading for exchange clients:

```
exchanges/
├── base.py              # BaseExchangeClient (abstract base class)
├── factory.py           # ExchangeFactory for dynamic instantiation
├── edgex.py             # EdgeX implementation
├── backpack.py          # Backpack implementation
├── paradex.py           # Paradex implementation
├── aster.py             # Aster implementation
└── lighter.py           # Lighter implementation
```

All exchange clients must inherit from `BaseExchangeClient` and implement:
- `connect()` / `disconnect()` - Connection management
- `place_open_order()` - Place opening order
- `place_close_order()` - Place take-profit order
- `place_market_order()` - Place market order (used in Aster Boost mode)
- `cancel_order()` - Cancel order
- `get_order_info()` - Fetch order details
- `get_active_orders()` - List active orders for contract
- `get_account_positions()` - Fetch current position (returns Decimal)
- `setup_order_update_handler()` - Register WebSocket callback
- `get_contract_attributes()` - Get contract ID and tick size (returns Tuple[str, Decimal])
- `fetch_bbo_prices()` - Get best bid/offer (returns Tuple[Decimal, Decimal])
- `get_order_price()` - Get appropriate order price for direction (returns Decimal)
- `get_exchange_name()` - Return exchange name (returns str)

See `docs/ADDING_EXCHANGES.md` for detailed instructions on adding new exchanges.

### Data Structures

**OrderResult** (returned by order operations):
```python
success: bool
order_id: str
side: str  # 'buy' or 'sell'
size: Decimal
price: Decimal
status: str  # 'OPEN', 'FILLED', 'CANCELED', etc.
error_message: str
filled_size: Decimal
```

**OrderInfo** (order state):
```python
order_id: str
side: str
size: Decimal
price: Decimal
status: str
filled_size: Decimal
remaining_size: Decimal
cancel_reason: str
```

**TradingConfig** (bot configuration):
```python
ticker: str
contract_id: str
quantity: Decimal
take_profit: Decimal
direction: str  # 'buy' or 'sell'
max_orders: int
wait_time: int
exchange: str
grid_step: Decimal
stop_price: Decimal
pause_price: Decimal
aster_boost: bool
```

### Trading Bot Flow

1. **Initialization**
   - Create exchange client via factory
   - Setup WebSocket handlers for order updates
   - Connect to exchange

2. **Main Loop** (in `trading_bot.py:run()`)
   - Fetch active orders
   - Check position/order mismatch (safety check)
   - Check stop/pause price conditions
   - Calculate wait time based on active orders
   - Check grid step condition (prevent dense orders)
   - Place open order and monitor fill
   - Place close order when open order fills

3. **Order Placement Logic** (`_place_and_monitor_open_order()`)
   - Place limit order near market price
   - Wait for fill or timeout (10 seconds)
   - If filled → place take-profit order
   - If not filled and price moved → cancel and retry
   - If partially filled → place take-profit for filled amount

4. **Safety Mechanisms**
   - Position mismatch detection: Alerts and stops if position ≠ active close orders
   - Grid step control: Prevents take-profit orders too close together
   - Stop/pause price: Exit or pause when price reaches threshold
   - Dynamic wait time: Slows down when many orders active

### Important Implementation Details

- **Async/Await**: All exchange operations are async
- **WebSocket Updates**: Order fills are detected via WebSocket callbacks, not polling
- **Thread Safety**: Uses `asyncio.Event` for coordination between WebSocket thread and main loop
- **Decimal Precision**: All prices/quantities use `Decimal` type
- **Retry Logic**: `query_retry` decorator on exchange methods (from `base.py`) - 5 retries with exponential backoff
- **Tick Size**: Prices are rounded to exchange tick size via `round_to_tick()` using `ROUND_HALF_UP`
- **Order Monitoring**: 10-second timeout waiting for order fills before checking status

### Logging

The bot creates multiple log files in the `logs/` directory:
- `<exchange>_<ticker>_orders.csv` - Trade log (CSV format)
- `<exchange>_<ticker>_activity.log` - Debug/activity log

If `ACCOUNT_NAME` environment variable is set:
- `<exchange>_<ticker>_<account_name>_orders.csv`
- `<exchange>_<ticker>_<account_name>_activity.log`

Helper class: `TradingLogger` (in `helpers/logger.py`)

Log timezone can be configured via `TIMEZONE` environment variable (default: `Asia/Shanghai`)

### Notifications

Optional Telegram/Lark notifications for critical events (position mismatch, shutdown, stop/pause price triggers).

**Telegram Configuration:**
- `TELEGRAM_BOT_TOKEN` - Your Telegram bot token
- `TELEGRAM_CHAT_ID` - Your Telegram chat ID

**Lark Configuration:**
- `LARK_TOKEN` - Your Lark bot webhook token

**General Configuration:**
- `ACCOUNT_NAME` - Optional account identifier for multi-account setups (used in logs and notifications)
- `TIMEZONE` - Log timestamp timezone (default: `Asia/Shanghai`)

## Common Issues

1. **Position Mismatch**: If position ≠ sum of close orders, bot will alert and stop. Manually close/cancel orders to rebalance. Check that `abs(position_amt - active_close_amount) <= 2 * quantity` to avoid triggering the safety mechanism.

2. **Partial Fills on Cancel**: When canceling an open order, bot checks `filled_size` and places take-profit for any partial fill. This is handled in `_handle_order_result()` method.

3. **Lighter Exchange**: Uses custom order tracking via `self.exchange_client.current_order` instead of `get_order_info()`. Also has special handling for `CANCELED-SELF-TRADE` status where it retries placing the close order.

4. **Aster Boost Mode**: Places maker open order, then immediately closes with taker order (fast volume but higher fees). Enabled with `--aster-boost` flag (only works with Aster exchange).

5. **Grid Step**: Set `--grid-step` to prevent take-profit orders clustering (e.g., 0.5% means next TP must be 0.5% away from existing TPs). Default is -100 (no restriction).

6. **WebSocket Connection**: Bot waits 5 seconds after connecting to exchange before entering main loop to ensure WebSocket is ready. Thread-safe coordination uses `asyncio.Event` (`order_filled_event`, `order_canceled_event`).

7. **Order Monitoring Timeout**: Open orders are monitored for 10 seconds before checking status. If not filled and price moved, order is canceled and retried.

## Multiple Accounts/Contracts

- **Multiple accounts, same exchange**: Create `account_1.env`, `account_2.env`, run with `--env-file account_1.env`
- **Multiple exchanges**: Configure all in same `.env`, use `--exchange` flag
- **Multiple contracts**: Use `--ticker` flag (e.g., ETH, BTC, SOL)

## Dependencies

Key packages:
- `asyncio` - Async runtime
- `aiohttp` - HTTP client for exchange APIs
- `websocket-client` / `websockets` - WebSocket connections
- `pycryptodome` - Cryptography for signing
- `tenacity` - Retry logic with exponential backoff
- `bpx-py` - Backpack SDK (v2.0.11)
- `edgex-python-sdk` - EdgeX SDK (forked version from @your-quantguy with post-only support)
- `lighter-python` - Lighter SDK (from @elliottech)
- `pytest` - Testing framework (for running tests)

Exchange-specific SDKs are generally NOT imported in `base.py` or `factory.py` to keep dependencies modular. The factory uses lazy loading via dynamic imports.

## Code Style

- Use async/await for all I/O operations
- Return `OrderResult` / `OrderInfo` from exchange methods
- Log important events via `self.logger.log(message, level)`
- Use `Decimal` for all financial calculations (never use `float` for prices/quantities)
- Handle exceptions gracefully and log tracebacks
- Use `query_retry` decorator for API calls that may fail transiently

## File Structure

```
perp-dex-tools/
├── runbot.py                    # Entry point, CLI argument parsing
├── trading_bot.py               # Main TradingBot class and trading logic
├── requirements.txt             # Python dependencies
├── para_requirements.txt        # Paradex-specific dependencies
├── .env                         # Environment variables (not in git)
├── env_example.txt             # Environment variable template
├── exchanges/
│   ├── __init__.py             # Module exports
│   ├── base.py                 # BaseExchangeClient, OrderResult, OrderInfo
│   ├── factory.py              # ExchangeFactory with lazy loading
│   ├── edgex.py                # EdgeX implementation
│   ├── backpack.py             # Backpack implementation
│   ├── paradex.py              # Paradex implementation
│   ├── aster.py                # Aster implementation
│   ├── lighter.py              # Lighter implementation
│   └── lighter_custom_websocket.py  # Custom WebSocket for Lighter
├── helpers/
│   ├── __init__.py             # Module exports
│   ├── logger.py               # TradingLogger class
│   ├── telegram_bot.py         # Telegram notifications
│   └── lark_bot.py             # Lark notifications
├── tests/
│   └── test_query_retry.py     # Tests for retry logic
├── logs/                        # Auto-generated log files
├── docs/
│   ├── ADDING_EXCHANGES.md     # Guide for adding new exchanges
│   ├── telegram-bot-setup.md   # Telegram setup (Chinese)
│   └── telegram-bot-setup-en.md  # Telegram setup (English)
└── README.md / README_EN.md    # User documentation
```