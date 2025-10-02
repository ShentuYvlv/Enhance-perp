"""
Hedge Volume Bot - Dual Account High-Frequency Hedge Trading for Volume Generation
"""

import os
import asyncio
import traceback
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple
from datetime import datetime

from exchanges.lighter import LighterClient
from helpers import TradingLogger
from helpers.lark_bot import LarkBot
from helpers.telegram_bot import TelegramBot


@dataclass
class HedgeConfig:
    """Configuration class for hedge trading parameters."""
    ticker: str
    margin: Decimal  # Margin per trade in USDC
    hold_time: int  # Position hold time in seconds
    take_profit: Decimal  # Take profit percentage (default 50%)
    stop_loss: Decimal  # Stop loss percentage (default 50%)
    contract_id: str = ''
    tick_size: Decimal = Decimal(0)


@dataclass
class PositionState:
    """Track position state for both accounts."""
    account1_order_id: Optional[str] = None
    account2_order_id: Optional[str] = None
    account1_entry_price: Optional[Decimal] = None
    account2_entry_price: Optional[Decimal] = None
    account1_quantity: Optional[Decimal] = None
    account2_quantity: Optional[Decimal] = None
    entry_time: Optional[float] = None
    is_open: bool = False


class HedgeVolumeBot:
    """Dual-account hedge trading bot for volume generation."""

    def __init__(self, config: HedgeConfig):
        self.config = config
        self.logger = TradingLogger("hedge", config.ticker, log_to_console=True)

        # Position tracking
        self.position = PositionState()
        self.shutdown_requested = False

        # Create two separate Lighter clients
        self.account1_client = None  # Will open LONG positions
        self.account2_client = None  # Will open SHORT positions

    def _create_lighter_config(self, ticker: str, account_suffix: str) -> dict:
        """Create configuration for a Lighter client instance."""
        # Create a minimal config object that LighterClient expects
        class MinimalConfig:
            def __init__(self, ticker):
                self.ticker = ticker
                self.contract_id = ''
                self.tick_size = Decimal(0)
                self.close_order_side = 'sell'  # Default, will be updated
                self.direction = 'buy'  # Default
                self.quantity = Decimal(0)
                self.take_profit = Decimal(0)
                self.exchange = 'lighter'
                self.market_info = None
                self.market_index = None
                self.account_index = None
                self.lighter_client = None

        return MinimalConfig(ticker)

    async def initialize(self):
        """Initialize both Lighter clients with separate credentials."""
        try:
            self.logger.log("Initializing dual Lighter accounts...", "INFO")

            # Store original environment variables
            original_api_key = os.getenv('API_KEY_PRIVATE_KEY')
            original_account_index = os.getenv('LIGHTER_ACCOUNT_INDEX')
            original_api_key_index = os.getenv('LIGHTER_API_KEY_INDEX')

            # Initialize Account 1 (LONG)
            os.environ['API_KEY_PRIVATE_KEY'] = os.getenv('API_KEY_PRIVATE_KEY_1')
            os.environ['LIGHTER_ACCOUNT_INDEX'] = os.getenv('LIGHTER_ACCOUNT_INDEX_1')
            os.environ['LIGHTER_API_KEY_INDEX'] = os.getenv('LIGHTER_API_KEY_INDEX_1')

            # First, get contract info using a temporary API client (without WebSocket)
            self.logger.log("Getting contract information...", "INFO")
            from lighter import ApiClient, Configuration, OrderApi
            temp_api_client = ApiClient(configuration=Configuration(host="https://mainnet.zklighter.elliot.ai"))
            order_api = OrderApi(temp_api_client)
            order_books = await order_api.order_books()

            # Find the market for our ticker
            market_info = None
            for market in order_books.order_books:
                if market.symbol == self.config.ticker:
                    self.config.contract_id = market.market_id
                    self.config.tick_size = Decimal("1") / (Decimal("10") ** market.supported_price_decimals)
                    market_info = market
                    self.logger.log(f"Found contract: {self.config.ticker} = Market ID {self.config.contract_id}", "INFO")
                    break

            await temp_api_client.close()

            if not self.config.contract_id:
                raise ValueError(f"Ticker {self.config.ticker} not found")

            # Now initialize clients with contract_id already set
            config1 = self._create_lighter_config(self.config.ticker, '1')
            config1.contract_id = self.config.contract_id
            config1.tick_size = self.config.tick_size
            self.account1_client = LighterClient(config1)

            # Set the multipliers from market_info
            self.account1_client.base_amount_multiplier = pow(10, market_info.supported_size_decimals)
            self.account1_client.price_multiplier = pow(10, market_info.supported_price_decimals)

            self.logger.log("Connecting Account 1 (LONG)...", "INFO")
            await self.account1_client.connect()

            # Initialize Account 2 (SHORT)
            os.environ['API_KEY_PRIVATE_KEY'] = os.getenv('API_KEY_PRIVATE_KEY_2')
            os.environ['LIGHTER_ACCOUNT_INDEX'] = os.getenv('LIGHTER_ACCOUNT_INDEX_2')
            os.environ['LIGHTER_API_KEY_INDEX'] = os.getenv('LIGHTER_API_KEY_INDEX_2')

            # Initialize Account 2 with same contract info
            config2 = self._create_lighter_config(self.config.ticker, '2')
            config2.contract_id = self.config.contract_id
            config2.tick_size = self.config.tick_size
            self.account2_client = LighterClient(config2)

            # Set the multipliers from market_info
            self.account2_client.base_amount_multiplier = pow(10, market_info.supported_size_decimals)
            self.account2_client.price_multiplier = pow(10, market_info.supported_price_decimals)

            self.logger.log("Connecting Account 2 (SHORT)...", "INFO")
            await self.account2_client.connect()

            # Restore original environment variables
            if original_api_key:
                os.environ['API_KEY_PRIVATE_KEY'] = original_api_key
            if original_account_index:
                os.environ['LIGHTER_ACCOUNT_INDEX'] = original_account_index
            if original_api_key_index:
                os.environ['LIGHTER_API_KEY_INDEX'] = original_api_key_index

            # Wait for WebSocket connections to be fully established
            self.logger.log("Waiting for WebSocket connections to establish...", "INFO")
            await asyncio.sleep(10)

            # Verify WebSocket connections are ready
            max_retries = 10
            for i in range(max_retries):
                if (hasattr(self.account1_client, 'ws_manager') and
                    self.account1_client.ws_manager and
                    self.account1_client.ws_manager.best_bid and
                    hasattr(self.account2_client, 'ws_manager') and
                    self.account2_client.ws_manager and
                    self.account2_client.ws_manager.best_bid):
                    self.logger.log("WebSocket connections established and data streaming", "INFO")
                    break
                else:
                    self.logger.log(f"Waiting for WebSocket data... ({i+1}/{max_retries})", "INFO")
                    await asyncio.sleep(2)

            self.logger.log("Both accounts initialized successfully", "INFO")

        except Exception as e:
            self.logger.log(f"Error initializing accounts: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            raise

    async def _calculate_quantity_from_margin(self, current_price: Decimal) -> Decimal:
        """Calculate position quantity based on margin and current price.

        Args:
            current_price: Current market price

        Returns:
            Position quantity
        """
        # Ensure current_price is Decimal
        current_price = Decimal(str(current_price))

        # Simple calculation: quantity = margin / price
        # Adjust this based on your leverage and risk management needs
        quantity = self.config.margin / current_price

        # Round to appropriate precision
        quantity = quantity.quantize(Decimal('0.0001'))

        self.logger.log(f"Calculated quantity: {quantity} (margin={self.config.margin}, price={current_price})", "INFO")
        return quantity

    async def _open_hedge_positions(self) -> bool:
        """Open hedged positions on both accounts simultaneously.

        Returns:
            True if both positions opened successfully, False otherwise
        """
        try:
            self.logger.log("=== Opening Hedge Positions ===", "INFO")

            # Get current market price
            best_bid, best_ask = await self.account1_client.fetch_bbo_prices(self.config.contract_id)
            # Ensure Decimal types
            best_bid = Decimal(str(best_bid))
            best_ask = Decimal(str(best_ask))
            mid_price = (best_bid + best_ask) / Decimal('2')

            # Validate price
            if mid_price <= 0:
                self.logger.log(f"Invalid market price: {mid_price} (bid={best_bid}, ask={best_ask})", "ERROR")
                return False

            # Calculate quantity based on margin
            quantity = await self._calculate_quantity_from_margin(mid_price)

            self.logger.log(f"Target: {quantity} @ mid_price {mid_price} (bid={best_bid}, ask={best_ask})", "INFO")

            # Open positions concurrently
            results = await asyncio.gather(
                self.account1_client.place_market_order(self.config.contract_id, quantity, 'buy'),
                self.account2_client.place_market_order(self.config.contract_id, quantity, 'sell'),
                return_exceptions=True
            )

            account1_result, account2_result = results

            # Check for exceptions
            if isinstance(account1_result, Exception):
                self.logger.log(f"Account 1 (LONG) failed: {account1_result}", "ERROR")
                return False

            if isinstance(account2_result, Exception):
                self.logger.log(f"Account 2 (SHORT) failed: {account2_result}", "ERROR")
                # Rollback Account 1
                await self._rollback_position(self.account1_client, account1_result, 'sell')
                return False

            # Check if both orders succeeded and filled
            if not account1_result.success or account1_result.status != 'FILLED':
                self.logger.log(f"Account 1 order not filled: status={account1_result.status}", "ERROR")
                return False

            if not account2_result.success or account2_result.status != 'FILLED':
                self.logger.log(f"Account 2 order not filled: status={account2_result.status}", "ERROR")
                # Rollback Account 1
                await self._rollback_position(self.account1_client, account1_result, 'sell')
                return False

            # Store position state
            self.position.account1_order_id = account1_result.order_id
            self.position.account2_order_id = account2_result.order_id
            self.position.account1_entry_price = account1_result.price
            self.position.account2_entry_price = account2_result.price
            self.position.account1_quantity = account1_result.filled_size or quantity
            self.position.account2_quantity = account2_result.filled_size or quantity
            self.position.entry_time = asyncio.get_event_loop().time()
            self.position.is_open = True

            self.logger.log(f"✓ Account 1 LONG: {self.position.account1_quantity} @ {self.position.account1_entry_price}", "INFO")
            self.logger.log(f"✓ Account 2 SHORT: {self.position.account2_quantity} @ {self.position.account2_entry_price}", "INFO")
            self.logger.log("=== Hedge Positions Opened Successfully ===", "INFO")

            return True

        except Exception as e:
            self.logger.log(f"Error opening hedge positions: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            return False

    async def _rollback_position(self, client: LighterClient, order_result, close_side: str):
        """Rollback a position by immediately closing it.

        Args:
            client: The Lighter client to use
            order_result: The order result to rollback
            close_side: Side to close ('buy' or 'sell')
        """
        try:
            self.logger.log(f"Rolling back position: {order_result.filled_size} @ {order_result.price}", "WARNING")

            if order_result.filled_size and order_result.filled_size > 0:
                await client.place_market_order(
                    self.config.contract_id,
                    order_result.filled_size,
                    close_side
                )
                self.logger.log("Rollback completed", "INFO")

        except Exception as e:
            self.logger.log(f"Error during rollback: {e}", "ERROR")
            await self.send_notification(f"⚠️ CRITICAL: Rollback failed for {client.get_exchange_name()}: {e}")

    async def _check_stop_conditions(self) -> Tuple[bool, str]:
        """Check if stop-loss or take-profit conditions are met.

        Returns:
            Tuple of (should_close, reason)
        """
        if not self.position.is_open:
            return False, ""

        try:
            # Get current market price
            best_bid, best_ask = await self.account1_client.fetch_bbo_prices(self.config.contract_id)
            # Ensure Decimal types
            best_bid = Decimal(str(best_bid))
            best_ask = Decimal(str(best_ask))
            current_price = (best_bid + best_ask) / Decimal('2')

            # Calculate P&L for Account 1 (LONG)
            account1_pnl_pct = ((current_price - self.position.account1_entry_price) /
                               self.position.account1_entry_price * Decimal('100'))

            # Calculate P&L for Account 2 (SHORT)
            account2_pnl_pct = ((self.position.account2_entry_price - current_price) /
                               self.position.account2_entry_price * Decimal('100'))

            self.logger.log(f"P&L: Account1={account1_pnl_pct:.2f}%, Account2={account2_pnl_pct:.2f}%", "INFO")

            # Check stop loss for either account
            stop_loss_threshold = -self.config.stop_loss
            if account1_pnl_pct <= stop_loss_threshold:
                return True, f"Account 1 Stop Loss triggered ({account1_pnl_pct:.2f}%)"

            if account2_pnl_pct <= stop_loss_threshold:
                return True, f"Account 2 Stop Loss triggered ({account2_pnl_pct:.2f}%)"

            # Check take profit for either account
            if account1_pnl_pct >= self.config.take_profit:
                return True, f"Account 1 Take Profit triggered ({account1_pnl_pct:.2f}%)"

            if account2_pnl_pct >= self.config.take_profit:
                return True, f"Account 2 Take Profit triggered ({account2_pnl_pct:.2f}%)"

            return False, ""

        except Exception as e:
            self.logger.log(f"Error checking stop conditions: {e}", "ERROR")
            return False, ""

    async def _close_hedge_positions(self):
        """Close hedged positions on both accounts simultaneously."""
        try:
            self.logger.log("=== Closing Hedge Positions ===", "INFO")

            if not self.position.is_open:
                self.logger.log("No open positions to close", "WARNING")
                return

            # Close positions concurrently (reverse sides)
            results = await asyncio.gather(
                self.account1_client.place_market_order(
                    self.config.contract_id,
                    self.position.account1_quantity,
                    'sell'  # Close LONG
                ),
                self.account2_client.place_market_order(
                    self.config.contract_id,
                    self.position.account2_quantity,
                    'buy'  # Close SHORT
                ),
                return_exceptions=True
            )

            account1_close, account2_close = results

            # Log results
            if isinstance(account1_close, Exception):
                self.logger.log(f"Account 1 close failed: {account1_close}", "ERROR")
            else:
                self.logger.log(f"✓ Account 1 closed: {account1_close.filled_size} @ {account1_close.price}", "INFO")

            if isinstance(account2_close, Exception):
                self.logger.log(f"Account 2 close failed: {account2_close}", "ERROR")
            else:
                self.logger.log(f"✓ Account 2 closed: {account2_close.filled_size} @ {account2_close.price}", "INFO")

            # Reset position state
            self.position = PositionState()

            self.logger.log("=== Hedge Positions Closed ===", "INFO")

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
        """Main trading loop - continuous hedge cycle."""
        try:
            # Initialize clients
            await self.initialize()

            # Log configuration
            self.logger.log("=== Hedge Volume Bot Configuration ===", "INFO")
            self.logger.log(f"Ticker: {self.config.ticker}", "INFO")
            self.logger.log(f"Contract ID: {self.config.contract_id}", "INFO")
            self.logger.log(f"Margin per trade: {self.config.margin} USDC", "INFO")
            self.logger.log(f"Hold time: {self.config.hold_time}s", "INFO")
            self.logger.log(f"Take Profit: {self.config.take_profit}%", "INFO")
            self.logger.log(f"Stop Loss: {self.config.stop_loss}%", "INFO")
            self.logger.log("======================================", "INFO")

            # Continuous trading loop
            while not self.shutdown_requested:
                try:
                    # Open hedge positions
                    success = await self._open_hedge_positions()

                    if not success:
                        self.logger.log("Failed to open positions, retrying in 10 seconds...", "WARNING")
                        await asyncio.sleep(10)
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

                    # Brief pause before next cycle
                    self.logger.log("Waiting 5 seconds before next cycle...", "INFO")
                    await asyncio.sleep(5)

                except Exception as e:
                    self.logger.log(f"Error in trading cycle: {e}", "ERROR")
                    self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
                    await asyncio.sleep(10)

        except KeyboardInterrupt:
            self.logger.log("Bot stopped by user", "INFO")
        except Exception as e:
            self.logger.log(f"Critical error: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            await self.send_notification(f"⚠️ Hedge Bot Critical Error: {e}")
        finally:
            # Cleanup
            try:
                if self.position.is_open:
                    self.logger.log("Closing open positions before shutdown...", "INFO")
                    await self._close_hedge_positions()

                if self.account1_client:
                    await self.account1_client.disconnect()
                if self.account2_client:
                    await self.account2_client.disconnect()

                self.logger.log("Hedge bot shutdown complete", "INFO")
            except Exception as e:
                self.logger.log(f"Error during shutdown: {e}", "ERROR")
