"""
Cross-Exchange Hedge Bot - Paradex ↔ Lighter Hedging for Volume Generation
"""

import os
import asyncio
import traceback
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

from exchanges.paradex import ParadexClient
from exchanges.lighter import LighterClient
from helpers import TradingLogger
from helpers.lark_bot import LarkBot
from helpers.telegram_bot import TelegramBot


@dataclass
class CrossHedgeConfig:
    """Configuration class for cross-exchange hedge trading parameters."""
    ticker: str
    margin: Decimal  # Margin per trade in USDC
    hold_time: int  # Position hold time in seconds
    take_profit: Decimal  # Take profit percentage (default 50%)
    stop_loss: Decimal  # Stop loss percentage (default 50%)
    reverse: bool = False  # Reverse direction (Paradex SHORT + Lighter LONG)
    contract_id: str = ''
    tick_size: Decimal = Decimal(0)


@dataclass
class CrossPositionState:
    """Track position state for both exchanges."""
    paradex_order_id: Optional[str] = None
    lighter_order_id: Optional[str] = None
    paradex_entry_price: Optional[Decimal] = None
    lighter_entry_price: Optional[Decimal] = None
    paradex_quantity: Optional[Decimal] = None
    lighter_quantity: Optional[Decimal] = None
    entry_time: Optional[float] = None
    is_open: bool = False


class CrossExchangeHedgeBot:
    """Cross-exchange hedge trading bot for volume generation (Paradex ↔ Lighter)."""

    def __init__(self, config: CrossHedgeConfig):
        self.config = config
        self.logger = TradingLogger("cross_hedge", config.ticker, log_to_console=True)

        # Position tracking
        self.position = CrossPositionState()
        self.shutdown_requested = False

        # Create separate clients for Paradex and Lighter
        self.paradex_client = None
        self.lighter_client = None

    def _create_client_config(self, ticker: str, exchange: str) -> object:
        """Create configuration for an exchange client instance."""
        class MinimalConfig:
            def __init__(self, ticker, exchange):
                self.ticker = ticker
                self.contract_id = ''
                self.tick_size = Decimal(0)
                self.close_order_side = 'sell'  # Default, will be updated
                self.direction = 'buy'  # Default
                self.quantity = Decimal(0)
                self.take_profit = Decimal(0)
                self.exchange = exchange
                self.market_info = None
                self.market_index = None
                self.account_index = None

        return MinimalConfig(ticker, exchange)

    async def initialize(self):
        """Initialize both Paradex and Lighter clients."""
        try:
            self.logger.log("Initializing Paradex and Lighter clients...", "INFO")

            # Initialize Paradex Client
            self.logger.log("Connecting to Paradex...", "INFO")
            paradex_config = self._create_client_config(self.config.ticker, 'paradex')
            self.paradex_client = ParadexClient(paradex_config)
            await self.paradex_client.connect()

            # Get Paradex contract attributes
            paradex_contract_id, paradex_tick_size = await self.paradex_client.get_contract_attributes()
            self.logger.log(f"Paradex: {self.config.ticker} = {paradex_contract_id}", "INFO")

            # Initialize Lighter Client
            self.logger.log("Connecting to Lighter...", "INFO")

            # Debug: Print Lighter credentials
            import os
            lighter_account_index = os.getenv('LIGHTER_ACCOUNT_INDEX', 'NOT SET')
            lighter_api_key_index = os.getenv('LIGHTER_API_KEY_INDEX', 'NOT SET')
            api_key_exists = 'YES' if os.getenv('API_KEY_PRIVATE_KEY') else 'NO'
            self.logger.log(f"[DEBUG] Lighter Config: ACCOUNT_INDEX={lighter_account_index}, "
                          f"API_KEY_INDEX={lighter_api_key_index}, API_KEY_EXISTS={api_key_exists}", "INFO")

            # Create Lighter config
            lighter_config = self._create_client_config(self.config.ticker, 'lighter')
            self.lighter_client = LighterClient(lighter_config)

            # CRITICAL: Get contract_id BEFORE connecting to ensure WebSocket subscribes to correct channel
            # This must be done before connect() because WebSocket uses contract_id for subscription
            lighter_contract_id, lighter_tick_size = await self.lighter_client.get_contract_attributes()
            self.logger.log(f"Lighter: {self.config.ticker} = Market ID {lighter_contract_id}", "INFO")

            # Set contract_id in client config BEFORE connecting
            self.lighter_client.config.contract_id = lighter_contract_id
            self.lighter_client.config.tick_size = lighter_tick_size

            # Now connect with correct contract_id set
            await self.lighter_client.connect()

            # Store contract info (use Paradex's for general config)
            self.config.contract_id = paradex_contract_id
            self.config.tick_size = paradex_tick_size

            # Wait for WebSocket connections to be fully established
            self.logger.log("Waiting for WebSocket connections to establish...", "INFO")
            await asyncio.sleep(10)

            # Verify WebSocket connections are ready
            max_retries = 10
            for i in range(max_retries):
                paradex_ready = True  # Paradex usually ready immediately
                lighter_ready = (hasattr(self.lighter_client, 'ws_manager') and
                                self.lighter_client.ws_manager and
                                self.lighter_client.ws_manager.best_bid)

                if lighter_ready:
                    self.logger.log("WebSocket connections established and data streaming", "INFO")
                    break
                else:
                    self.logger.log(f"Waiting for Lighter WebSocket data... ({i+1}/{max_retries})", "INFO")
                    await asyncio.sleep(2)

            if not lighter_ready:
                self.logger.log("Warning: Lighter WebSocket may not be fully ready", "WARNING")

            # Note: Balance check removed - leverage is set at exchange account level
            # Exchange APIs will return error if insufficient margin for the position
            # This allows users with high leverage (e.g., 35x) to trade with smaller balances

            self.logger.log("Both exchanges initialized successfully", "INFO")

        except Exception as e:
            self.logger.log(f"Error initializing exchanges: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            raise

    async def _check_account_balances(self):
        """Check account balances on both exchanges and terminate if insufficient."""
        try:
            # Get balances
            paradex_balance = await self.paradex_client.get_account_balance()

            # ========== Lighter Balance Debugging ==========
            import lighter
            account_api = lighter.AccountApi(self.lighter_client.api_client)
            account_data = await account_api.account(
                by="index",
                value=str(self.lighter_client.account_index)
            )

            self.logger.log(f"[DEBUG] Lighter API Response:", "INFO")
            self.logger.log(f"  - account_data type: {type(account_data)}", "INFO")
            self.logger.log(f"  - account_data.total: {account_data.total}", "INFO")
            self.logger.log(f"  - account_data.accounts length: {len(account_data.accounts)}", "INFO")

            if account_data.accounts:
                acc = account_data.accounts[0]
                self.logger.log(f"  - available_balance: {acc.available_balance}", "INFO")
                self.logger.log(f"  - collateral: {acc.collateral}", "INFO")
                self.logger.log(f"  - total_asset_value: {acc.total_asset_value}", "INFO")
                self.logger.log(f"  - cross_asset_value: {acc.cross_asset_value}", "INFO")

                # Test different interpretations
                if acc.available_balance is not None:
                    try:
                        avail_as_decimal = Decimal(acc.available_balance)
                        avail_divided = avail_as_decimal / Decimal('1e6')
                        self.logger.log(f"  - available_balance as Decimal: {avail_as_decimal}", "INFO")
                        self.logger.log(f"  - available_balance / 1e6: {avail_divided}", "INFO")
                    except Exception as e:
                        self.logger.log(f"  - Error parsing available_balance: {e}", "ERROR")

                try:
                    collateral_as_decimal = Decimal(acc.collateral)
                    collateral_divided = collateral_as_decimal / Decimal('1e6')
                    self.logger.log(f"  - collateral as Decimal: {collateral_as_decimal}", "INFO")
                    self.logger.log(f"  - collateral / 1e6: {collateral_divided}", "INFO")
                except Exception as e:
                    self.logger.log(f"  - Error parsing collateral: {e}", "ERROR")

                try:
                    total_asset_as_decimal = Decimal(acc.total_asset_value)
                    total_asset_divided = total_asset_as_decimal / Decimal('1e6')
                    self.logger.log(f"  - total_asset_value as Decimal: {total_asset_as_decimal}", "INFO")
                    self.logger.log(f"  - total_asset_value / 1e6: {total_asset_divided}", "INFO")
                except Exception as e:
                    self.logger.log(f"  - Error parsing total_asset_value: {e}", "ERROR")
            # ========== End Debugging ==========

            lighter_balance = await self.lighter_client.get_account_balance()

            # Calculate required balance with more reasonable safety margin
            # 1.2x accounts for: initial margin + maintenance margin buffer + fees + slippage
            # Original 2x was overly conservative and blocked valid trades
            required_balance = self.config.margin * Decimal('1.2')

            self.logger.log(f"Paradex balance: {paradex_balance} USDC (required: {required_balance})", "INFO")
            self.logger.log(f"Lighter balance: {lighter_balance} USDC (required: {required_balance})", "INFO")

            # Check if either exchange has insufficient balance
            if paradex_balance < required_balance:
                error_msg = (f"Insufficient Paradex balance: {paradex_balance} USDC "
                            f"(required: {required_balance} USDC)")
                self.logger.log(error_msg, "ERROR")
                await self.send_notification(f"⚠️ {error_msg}")
                raise ValueError(error_msg)

            if lighter_balance < required_balance:
                error_msg = (f"Insufficient Lighter balance: {lighter_balance} USDC "
                            f"(required: {required_balance} USDC)")
                self.logger.log(error_msg, "ERROR")
                await self.send_notification(f"⚠️ {error_msg}")
                raise ValueError(error_msg)

        except Exception as e:
            self.logger.log(f"Error checking account balances: {e}", "ERROR")
            raise

    async def _calculate_quantity_from_margin(self, avg_price: Decimal) -> Decimal:
        """Calculate position quantity based on margin and average price.

        Args:
            avg_price: Average market price across both exchanges

        Returns:
            Position quantity
        """
        # Ensure avg_price is Decimal
        avg_price = Decimal(str(avg_price))

        # Simple calculation: quantity = margin / price
        quantity = self.config.margin / avg_price

        # Round to appropriate precision
        quantity = quantity.quantize(Decimal('0.0001'))

        self.logger.log(f"Calculated quantity: {quantity} (margin={self.config.margin}, avg_price={avg_price})", "INFO")
        return quantity

    async def _get_average_price(self) -> Decimal:
        """Get average price across both exchanges.

        Returns:
            Average mid price
        """
        # Get Paradex prices
        paradex_bid, paradex_ask = await self.paradex_client.fetch_bbo_prices(self.config.contract_id)
        paradex_mid = (Decimal(str(paradex_bid)) + Decimal(str(paradex_ask))) / Decimal('2')

        # Get Lighter prices
        lighter_bid, lighter_ask = await self.lighter_client.fetch_bbo_prices(self.lighter_client.config.contract_id)
        lighter_mid = (Decimal(str(lighter_bid)) + Decimal(str(lighter_ask))) / Decimal('2')

        # Calculate average
        avg_mid = (paradex_mid + lighter_mid) / Decimal('2')

        self.logger.log(f"Prices: Paradex={paradex_mid}, Lighter={lighter_mid}, Avg={avg_mid}", "INFO")

        return avg_mid

    async def _open_hedge_positions(self) -> bool:
        """Open hedged positions on both exchanges simultaneously.

        Returns:
            True if both positions opened successfully, False otherwise
        """
        try:
            self.logger.log("=== Opening Cross-Exchange Hedge Positions ===", "INFO")

            # Get average market price
            avg_price = await self._get_average_price()

            # Validate price
            if avg_price <= 0:
                self.logger.log(f"Invalid average price: {avg_price}", "ERROR")
                return False

            # Calculate quantity based on margin
            quantity = await self._calculate_quantity_from_margin(avg_price)

            # Determine sides based on reverse flag
            if self.config.reverse:
                paradex_side = 'sell'  # Paradex SHORT
                lighter_side = 'buy'   # Lighter LONG
                self.logger.log(f"Reverse mode: Paradex SHORT + Lighter LONG", "INFO")
            else:
                paradex_side = 'buy'   # Paradex LONG
                lighter_side = 'sell'  # Lighter SHORT
                self.logger.log(f"Normal mode: Paradex LONG + Lighter SHORT", "INFO")

            self.logger.log(f"Target: {quantity} @ avg_price {avg_price}", "INFO")

            # Open positions concurrently
            results = await asyncio.gather(
                self.paradex_client.place_market_order(self.config.contract_id, quantity, paradex_side),
                self.lighter_client.place_market_order(self.lighter_client.config.contract_id, quantity, lighter_side),
                return_exceptions=True
            )

            paradex_result, lighter_result = results

            # Check for exceptions
            if isinstance(paradex_result, Exception):
                self.logger.log(f"Paradex ({paradex_side.upper()}) failed: {paradex_result}", "ERROR")
                return False

            if isinstance(lighter_result, Exception):
                self.logger.log(f"Lighter ({lighter_side.upper()}) failed: {lighter_result}", "ERROR")
                # Rollback Paradex
                await self._rollback_paradex_position(paradex_result, 'sell' if paradex_side == 'buy' else 'buy')
                return False

            # Check if both orders succeeded and filled
            if not paradex_result.success or paradex_result.status != 'FILLED':
                self.logger.log(f"Paradex order not filled: status={paradex_result.status}", "ERROR")
                # Try to rollback if partial fill
                if paradex_result.filled_size and paradex_result.filled_size > 0:
                    await self._rollback_paradex_position(paradex_result, 'sell' if paradex_side == 'buy' else 'buy')
                return False

            if not lighter_result.success or lighter_result.status != 'FILLED':
                self.logger.log(f"Lighter order not filled: status={lighter_result.status}", "ERROR")
                # Rollback Paradex
                await self._rollback_paradex_position(paradex_result, 'sell' if paradex_side == 'buy' else 'buy')
                return False

            # Store position state
            self.position.paradex_order_id = paradex_result.order_id
            self.position.lighter_order_id = lighter_result.order_id
            self.position.paradex_entry_price = paradex_result.price
            self.position.lighter_entry_price = lighter_result.price
            self.position.paradex_quantity = paradex_result.filled_size or quantity
            self.position.lighter_quantity = lighter_result.filled_size or quantity
            self.position.entry_time = asyncio.get_event_loop().time()
            self.position.is_open = True

            self.logger.log(f"✓ Paradex {paradex_side.upper()}: {self.position.paradex_quantity} @ {self.position.paradex_entry_price}", "INFO")
            self.logger.log(f"✓ Lighter {lighter_side.upper()}: {self.position.lighter_quantity} @ {self.position.lighter_entry_price}", "INFO")
            self.logger.log("=== Cross-Exchange Hedge Positions Opened Successfully ===", "INFO")

            return True

        except Exception as e:
            self.logger.log(f"Error opening hedge positions: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            return False

    async def _rollback_paradex_position(self, order_result, close_side: str):
        """Rollback a Paradex position by immediately closing it.

        Args:
            order_result: The order result to rollback
            close_side: Side to close ('buy' or 'sell')
        """
        try:
            self.logger.log(f"Rolling back Paradex position: {order_result.filled_size} @ {order_result.price}", "WARNING")

            if order_result.filled_size and order_result.filled_size > 0:
                await self.paradex_client.place_market_order(
                    self.config.contract_id,
                    order_result.filled_size,
                    close_side
                )
                self.logger.log("Paradex rollback completed", "INFO")

        except Exception as e:
            self.logger.log(f"Error during Paradex rollback: {e}", "ERROR")
            await self.send_notification(f"⚠️ CRITICAL: Paradex rollback failed: {e}")

    async def _check_stop_conditions(self) -> Tuple[bool, str]:
        """Check if stop-loss or take-profit conditions are met.

        Returns:
            Tuple of (should_close, reason)
        """
        if not self.position.is_open:
            return False, ""

        try:
            # Get current prices
            paradex_bid, paradex_ask = await self.paradex_client.fetch_bbo_prices(self.config.contract_id)
            paradex_price = (Decimal(str(paradex_bid)) + Decimal(str(paradex_ask))) / Decimal('2')

            lighter_bid, lighter_ask = await self.lighter_client.fetch_bbo_prices(self.lighter_client.config.contract_id)
            lighter_price = (Decimal(str(lighter_bid)) + Decimal(str(lighter_ask))) / Decimal('2')

            # Calculate P&L based on direction
            if self.config.reverse:
                # Paradex SHORT + Lighter LONG
                paradex_pnl_pct = ((self.position.paradex_entry_price - paradex_price) /
                                   self.position.paradex_entry_price * Decimal('100'))
                lighter_pnl_pct = ((lighter_price - self.position.lighter_entry_price) /
                                   self.position.lighter_entry_price * Decimal('100'))
            else:
                # Paradex LONG + Lighter SHORT
                paradex_pnl_pct = ((paradex_price - self.position.paradex_entry_price) /
                                   self.position.paradex_entry_price * Decimal('100'))
                lighter_pnl_pct = ((self.position.lighter_entry_price - lighter_price) /
                                   self.position.lighter_entry_price * Decimal('100'))

            self.logger.log(f"P&L: Paradex={paradex_pnl_pct:.2f}%, Lighter={lighter_pnl_pct:.2f}%", "INFO")

            # Check stop loss for either exchange
            stop_loss_threshold = -self.config.stop_loss
            if paradex_pnl_pct <= stop_loss_threshold:
                return True, f"Paradex Stop Loss triggered ({paradex_pnl_pct:.2f}%)"

            if lighter_pnl_pct <= stop_loss_threshold:
                return True, f"Lighter Stop Loss triggered ({lighter_pnl_pct:.2f}%)"

            # Check take profit for either exchange
            if paradex_pnl_pct >= self.config.take_profit:
                return True, f"Paradex Take Profit triggered ({paradex_pnl_pct:.2f}%)"

            if lighter_pnl_pct >= self.config.take_profit:
                return True, f"Lighter Take Profit triggered ({lighter_pnl_pct:.2f}%)"

            return False, ""

        except Exception as e:
            self.logger.log(f"Error checking stop conditions: {e}", "ERROR")
            return False, ""

    async def _close_hedge_positions(self):
        """Close hedged positions on both exchanges simultaneously."""
        try:
            self.logger.log("=== Closing Cross-Exchange Hedge Positions ===", "INFO")

            if not self.position.is_open:
                self.logger.log("No open positions to close", "WARNING")
                return

            # Determine close sides based on reverse flag
            if self.config.reverse:
                paradex_close_side = 'buy'   # Close SHORT
                lighter_close_side = 'sell'  # Close LONG
            else:
                paradex_close_side = 'sell'  # Close LONG
                lighter_close_side = 'buy'   # Close SHORT

            # Close positions concurrently
            results = await asyncio.gather(
                self.paradex_client.place_market_order(
                    self.config.contract_id,
                    self.position.paradex_quantity,
                    paradex_close_side
                ),
                self.lighter_client.place_market_order(
                    self.lighter_client.config.contract_id,
                    self.position.lighter_quantity,
                    lighter_close_side
                ),
                return_exceptions=True
            )

            paradex_close, lighter_close = results

            # Log results
            if isinstance(paradex_close, Exception):
                self.logger.log(f"Paradex close failed: {paradex_close}", "ERROR")
            else:
                self.logger.log(f"✓ Paradex closed: {paradex_close.filled_size} @ {paradex_close.price}", "INFO")

            if isinstance(lighter_close, Exception):
                self.logger.log(f"Lighter close failed: {lighter_close}", "ERROR")
            else:
                self.logger.log(f"✓ Lighter closed: {lighter_close.filled_size} @ {lighter_close.price}", "INFO")

            # Reset position state
            self.position = CrossPositionState()

            self.logger.log("=== Cross-Exchange Hedge Positions Closed ===", "INFO")

        except Exception as e:
            self.logger.log(f"Error closing hedge positions: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")

    async def send_notification(self, message: str):
        """Send notification via Telegram/Lark."""
        lark_token = os.getenv("LARK_TOKEN")
        if lark_token:
            async with LarkBot(lark_token) as lark_bot:
                await lark_bot.send_text(message)

        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if telegram_token and telegram_chat_id:
            with TelegramBot(telegram_token, telegram_chat_id) as tg_bot:
                tg_bot.send_text(message)

    async def run(self):
        """Main trading loop - continuous hedge cycle with 20s interval."""
        try:
            # Initialize clients
            await self.initialize()

            # Log configuration
            self.logger.log("=== Cross-Exchange Hedge Bot Configuration ===", "INFO")
            self.logger.log(f"Ticker: {self.config.ticker}", "INFO")
            self.logger.log(f"Paradex Contract: {self.config.contract_id}", "INFO")
            self.logger.log(f"Lighter Contract: {self.lighter_client.config.contract_id}", "INFO")
            self.logger.log(f"Margin per trade: {self.config.margin} USDC", "INFO")
            self.logger.log(f"Hold time: {self.config.hold_time}s", "INFO")
            self.logger.log(f"Take Profit: {self.config.take_profit}%", "INFO")
            self.logger.log(f"Stop Loss: {self.config.stop_loss}%", "INFO")
            self.logger.log(f"Reverse mode: {self.config.reverse}", "INFO")
            self.logger.log("==============================================", "INFO")

            # Continuous trading loop with 20s interval
            while not self.shutdown_requested:
                try:
                    # Open hedge positions
                    success = await self._open_hedge_positions()

                    if not success:
                        self.logger.log("Failed to open positions, retrying in 20 seconds...", "WARNING")
                        await asyncio.sleep(20)
                        continue

                    # Monitor position until hold time expires or stop conditions met
                    start_time = asyncio.get_event_loop().time()

                    while self.position.is_open and not self.shutdown_requested:
                        # Check time
                        elapsed = asyncio.get_event_loop().time() - start_time

                        if elapsed >= self.config.hold_time:
                            self.logger.log(f"Hold time expired ({self.config.hold_time}s)", "INFO")
                            break

                        # Check stop conditions
                        should_close, reason = await self._check_stop_conditions()
                        if should_close:
                            self.logger.log(f"Stop condition met: {reason}", "INFO")
                            break

                        # Sleep and check again
                        await asyncio.sleep(1)

                    # Close positions
                    await self._close_hedge_positions()

                    # Wait 20 seconds before next cycle
                    self.logger.log("Waiting 20 seconds before next cycle...", "INFO")
                    await asyncio.sleep(20)

                except Exception as e:
                    self.logger.log(f"Error in trading cycle: {e}", "ERROR")
                    self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
                    await asyncio.sleep(20)

        except KeyboardInterrupt:
            self.logger.log("Bot stopped by user", "INFO")
        except Exception as e:
            self.logger.log(f"Critical error: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            await self.send_notification(f"⚠️ Cross-Exchange Hedge Bot Critical Error: {e}")
        finally:
            # Cleanup
            try:
                if self.position.is_open:
                    self.logger.log("Closing open positions before shutdown...", "INFO")
                    await self._close_hedge_positions()

                if self.paradex_client:
                    await self.paradex_client.disconnect()
                if self.lighter_client:
                    await self.lighter_client.disconnect()

                self.logger.log("Cross-exchange hedge bot shutdown complete", "INFO")
            except Exception as e:
                self.logger.log(f"Error during shutdown: {e}", "ERROR")
