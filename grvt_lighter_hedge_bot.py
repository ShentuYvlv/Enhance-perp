"""
Cross-Exchange Hedge Bot - GRVT ↔ Lighter Hedging for Volume Generation
"""

import os
import asyncio
import traceback
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

from exchanges.grvt import GrvtClient
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
    take_profit: Decimal  # Take profit percentage (default 50%, deprecated - use max_profit_usdc)
    stop_loss: Decimal  # Stop loss percentage (default 50%, deprecated - use max_loss_usdc)
    max_loss_usdc: Decimal = Decimal('50')  # Maximum loss in USDC before stop loss
    max_profit_usdc: Decimal = Decimal('100')  # Maximum profit in USDC for take profit (optional)
    reverse: bool = False  # Reverse direction (GRVT SHORT + Lighter LONG)
    cycle_wait: int = 20  # Wait time between trading cycles in seconds
    contract_id: str = ''
    tick_size: Decimal = Decimal(0)


@dataclass
class CrossPositionState:
    """Track position state for both exchanges."""
    grvt_order_id: Optional[str] = None
    lighter_order_id: Optional[str] = None
    grvt_entry_price: Optional[Decimal] = None
    lighter_entry_price: Optional[Decimal] = None
    grvt_quantity: Optional[Decimal] = None
    lighter_quantity: Optional[Decimal] = None
    entry_time: Optional[float] = None
    is_open: bool = False


class GrvtLighterHedgeBot:
    """Cross-exchange hedge trading bot for volume generation (GRVT ↔ Lighter)."""

    def __init__(self, config: CrossHedgeConfig):
        self.config = config
        self.logger = TradingLogger("grvt_lighter_hedge", config.ticker, log_to_console=True)

        # Position tracking
        self.position = CrossPositionState()
        self.shutdown_requested = False

        # Create separate clients for GRVT and Lighter
        self.grvt_client = None
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
        """Initialize both GRVT and Lighter clients."""
        try:
            self.logger.log("Initializing GRVT and Lighter clients...", "INFO")

            # Initialize GRVT Client
            self.logger.log("Connecting to GRVT...", "INFO")
            grvt_config = self._create_client_config(self.config.ticker, 'grvt')
            self.grvt_client = GrvtClient(grvt_config)
            await self.grvt_client.connect()

            # Get GRVT contract attributes
            grvt_contract_id, grvt_tick_size = await self.grvt_client.get_contract_attributes()
            self.logger.log(f"GRVT: {self.config.ticker} = {grvt_contract_id}", "INFO")

            # Initialize Lighter Client
            self.logger.log("Connecting to Lighter...", "INFO")

            # Create Lighter config
            lighter_config = self._create_client_config(self.config.ticker, 'lighter')
            self.lighter_client = LighterClient(lighter_config)

            # CRITICAL: Get contract_id BEFORE connecting to ensure WebSocket subscribes to correct channel
            lighter_contract_id, lighter_tick_size = await self.lighter_client.get_contract_attributes()
            self.logger.log(f"Lighter: {self.config.ticker} = Market ID {lighter_contract_id}", "INFO")

            # Set contract_id in client config BEFORE connecting
            self.lighter_client.config.contract_id = lighter_contract_id
            self.lighter_client.config.tick_size = lighter_tick_size

            # Now connect with correct contract_id set
            await self.lighter_client.connect()

            # Store contract info (use GRVT's for general config)
            self.config.contract_id = grvt_contract_id
            self.config.tick_size = grvt_tick_size

            # Wait for WebSocket connections to be fully established
            self.logger.log("Waiting for WebSocket connections to establish...", "INFO")
            await asyncio.sleep(10)

            # Verify WebSocket connections are ready
            max_retries = 10
            for i in range(max_retries):
                grvt_ready = True  # GRVT usually ready immediately
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

            self.logger.log("Both exchanges initialized successfully", "INFO")

        except Exception as e:
            self.logger.log(f"Error initializing exchanges: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            raise

    async def _calculate_quantity_from_margin(self, target_price: Decimal, order_side: str) -> Decimal:
        """Calculate position quantity based on margin and GRVT's actual order price.

        This method ensures that the actual notional value on GRVT matches the target margin,
        accounting for order_size_increment precision truncation.

        Args:
            target_price: GRVT's actual order price (not average)
            order_side: 'buy' or 'sell' to handle precision rounding correctly

        Returns:
            Position quantity (adjusted for GRVT's order_size_increment precision)
        """
        # Ensure target_price is Decimal
        target_price = Decimal(str(target_price))

        # Calculate raw quantity based on target margin
        raw_quantity = self.config.margin / target_price

        # Get GRVT's order size increment (precision requirement)
        grvt_increment = self.grvt_client.order_size_increment

        # CRITICAL: Use ROUND_DOWN to ensure we don't exceed the margin budget
        from decimal import ROUND_DOWN
        adjusted_quantity = raw_quantity.quantize(grvt_increment, rounding=ROUND_DOWN)

        # Calculate actual notional value after precision truncation
        actual_notional = adjusted_quantity * target_price

        # Log detailed calculation for monitoring
        self.logger.log(
            f"Quantity calculation: margin={self.config.margin} USDC, price={target_price}, "
            f"raw_qty={raw_quantity:.8f}, adjusted_qty={adjusted_quantity}, "
            f"actual_notional={actual_notional:.2f} USDC, increment={grvt_increment}",
            "INFO"
        )

        # Warn if precision truncation causes significant deviation
        deviation_pct = abs(actual_notional - self.config.margin) / self.config.margin * Decimal('100')
        if deviation_pct > Decimal('15'):
            self.logger.log(
                f"⚠️ Precision truncation warning: actual notional deviates {deviation_pct:.2f}% from target margin",
                "WARNING"
            )

        return adjusted_quantity

    async def _get_average_price(self) -> Decimal:
        """Get average price across both exchanges (for monitoring/logging only).

        NOTE: This method is NOT used for quantity calculation anymore.
        Quantity is calculated using GRVT's actual order price to ensure accurate notional value.

        Returns:
            Average mid price (for reference only)
        """
        # Get GRVT prices
        grvt_bid, grvt_ask = await self.grvt_client.fetch_bbo_prices(self.config.contract_id)
        grvt_mid = (Decimal(str(grvt_bid)) + Decimal(str(grvt_ask))) / Decimal('2')

        # Get Lighter prices
        lighter_bid, lighter_ask = await self.lighter_client.fetch_bbo_prices(self.lighter_client.config.contract_id)
        lighter_mid = (Decimal(str(lighter_bid)) + Decimal(str(lighter_ask))) / Decimal('2')

        # Calculate average
        avg_mid = (grvt_mid + lighter_mid) / Decimal('2')

        self.logger.log(f"Prices: GRVT={grvt_mid}, Lighter={lighter_mid}, Avg={avg_mid}", "INFO")

        return avg_mid

    async def _open_hedge_positions(self) -> bool:
        """Open hedged positions - GRVT uses maker limit order, Lighter uses market order.

        Strategy:
        1. Place POST_ONLY limit order on GRVT (maker, low fees)
        2. Wait for GRVT order to fill
        3. Once filled, immediately hedge with Lighter market order (taker)

        Returns:
            True if both positions opened successfully, False otherwise
        """
        try:
            self.logger.log("=== Opening Cross-Exchange Hedge Positions ===", "INFO")

            # Determine sides based on reverse flag
            if self.config.reverse:
                grvt_side = 'sell'  # GRVT SHORT
                lighter_side = 'buy'   # Lighter LONG
                mode_desc = "Reverse mode: GRVT SHORT (maker) + Lighter LONG (taker)"
            else:
                grvt_side = 'buy'   # GRVT LONG
                lighter_side = 'sell'  # Lighter SHORT
                mode_desc = "Normal mode: GRVT LONG (maker) + Lighter SHORT (taker)"

            self.logger.log(mode_desc, "INFO")

            # ========== FIX: Use GRVT's actual order price for quantity calculation ==========
            # Get GRVT's BBO prices
            grvt_bid, grvt_ask = await self.grvt_client.fetch_bbo_prices(self.config.contract_id)

            # Calculate GRVT's actual order price (same logic as grvt.py:get_order_price)
            if grvt_side == 'buy':
                # For buy orders, place slightly below best ask to ensure maker
                grvt_order_price = grvt_ask - self.config.tick_size
            else:
                # For sell orders, place slightly above best bid to ensure maker
                grvt_order_price = grvt_bid + self.config.tick_size

            # Round to tick size
            grvt_order_price = self.grvt_client.round_to_tick(grvt_order_price)

            # Validate price
            if grvt_order_price <= 0:
                self.logger.log(f"Invalid GRVT order price: {grvt_order_price}", "ERROR")
                return False

            # Calculate quantity based on GRVT's actual order price (not average)
            quantity = await self._calculate_quantity_from_margin(grvt_order_price, grvt_side)

            # Get average price for monitoring/logging only
            avg_price = await self._get_average_price()

            self.logger.log(
                f"GRVT {grvt_side.upper()} target: {quantity} @ {grvt_order_price} "
                f"(bid={grvt_bid}, ask={grvt_ask}, avg_price={avg_price})",
                "INFO"
            )
            # ========== END FIX ==========

            # Step 1: Place POST_ONLY limit order on GRVT (maker)
            self.logger.log(f"Placing GRVT {grvt_side.upper()} maker order...", "INFO")
            try:
                grvt_result = await self.grvt_client.place_open_order(
                    self.config.contract_id, quantity, grvt_side
                )
            except Exception as e:
                self.logger.log(f"GRVT order placement failed: {e}", "ERROR")
                return False

            if not grvt_result.success:
                self.logger.log(f"GRVT order failed: {grvt_result.error_message}", "ERROR")
                return False

            # Step 2: Wait for GRVT order to fill (with timeout)
            self.logger.log(f"Waiting for GRVT order {grvt_result.order_id} to fill...", "INFO")
            timeout = 60  # 60 seconds timeout
            start_time = asyncio.get_event_loop().time()
            filled = False

            while asyncio.get_event_loop().time() - start_time < timeout:
                order_info = await self.grvt_client.get_order_info(order_id=grvt_result.order_id)

                # GRVT uses 'FILLED' status (not Paradex's 'CLOSED')
                if order_info and order_info.status == 'FILLED' and order_info.filled_size > 0:
                    filled = True
                    grvt_result.filled_size = order_info.filled_size
                    grvt_result.price = order_info.price
                    self.logger.log(f"✓ GRVT order filled: {order_info.filled_size} @ {order_info.price}", "INFO")
                    break

                await asyncio.sleep(0.5)

            if not filled:
                # Timeout - cancel GRVT order
                self.logger.log(f"GRVT order not filled within {timeout}s, cancelling...", "WARNING")
                await self.grvt_client.cancel_order(grvt_result.order_id)
                return False

            # Step 3: Immediately place Lighter market order to hedge
            self.logger.log(f"Placing Lighter {lighter_side.upper()} market order to hedge...", "INFO")
            try:
                lighter_result = await self.lighter_client.place_market_order(
                    self.lighter_client.config.contract_id,
                    grvt_result.filled_size,  # Use actual filled size from GRVT
                    lighter_side
                )
            except Exception as e:
                self.logger.log(f"Lighter hedge failed: {e}", "ERROR")
                # Rollback GRVT position
                await self._rollback_grvt_position(grvt_result, 'sell' if grvt_side == 'buy' else 'buy')
                return False

            # Check if Lighter order succeeded
            if not lighter_result.success or lighter_result.status != 'FILLED':
                self.logger.log(f"Lighter order not filled: status={lighter_result.status}", "ERROR")
                # Rollback GRVT
                await self._rollback_grvt_position(grvt_result, 'sell' if grvt_side == 'buy' else 'buy')
                return False

            # Store position state
            self.position.grvt_order_id = grvt_result.order_id
            self.position.lighter_order_id = lighter_result.order_id
            self.position.grvt_entry_price = grvt_result.price
            self.position.lighter_entry_price = lighter_result.price
            self.position.grvt_quantity = grvt_result.filled_size
            self.position.lighter_quantity = lighter_result.filled_size or grvt_result.filled_size
            self.position.entry_time = asyncio.get_event_loop().time()
            self.position.is_open = True

            # ========== Calculate and verify actual notional values ==========
            grvt_notional = self.position.grvt_quantity * self.position.grvt_entry_price
            lighter_notional = self.position.lighter_quantity * self.position.lighter_entry_price
            target_margin = self.config.margin

            # Calculate deviations
            grvt_deviation_pct = abs(grvt_notional - target_margin) / target_margin * Decimal('100')
            lighter_deviation_pct = abs(lighter_notional - target_margin) / target_margin * Decimal('100')

            self.logger.log(f"✓ GRVT {grvt_side.upper()} (maker): {self.position.grvt_quantity} @ {self.position.grvt_entry_price}", "INFO")
            self.logger.log(f"✓ Lighter {lighter_side.upper()} (taker): {self.position.lighter_quantity} @ {self.position.lighter_entry_price}", "INFO")

            # Log notional values
            self.logger.log(
                f"📊 Notional values - Target: {target_margin:.2f} USDC | "
                f"GRVT: {grvt_notional:.2f} USDC ({grvt_deviation_pct:+.2f}%) | "
                f"Lighter: {lighter_notional:.2f} USDC ({lighter_deviation_pct:+.2f}%)",
                "INFO"
            )

            # Warn if deviations are significant
            if grvt_deviation_pct > Decimal('15'):
                self.logger.log(
                    f"⚠️ GRVT notional deviation: {grvt_deviation_pct:.2f}% "
                    f"(actual: {grvt_notional:.2f}, target: {target_margin:.2f})",
                    "WARNING"
                )

            if lighter_deviation_pct > Decimal('15'):
                self.logger.log(
                    f"⚠️ Lighter notional deviation: {lighter_deviation_pct:.2f}% "
                    f"(actual: {lighter_notional:.2f}, target: {target_margin:.2f})",
                    "WARNING"
                )

            self.logger.log("=== Cross-Exchange Hedge Positions Opened Successfully ===", "INFO")
            # ========== END verification ==========

            return True

        except Exception as e:
            self.logger.log(f"Error opening hedge positions: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            return False

    async def _rollback_grvt_position(self, order_result, close_side: str):
        """Rollback a GRVT position by immediately closing it.

        Args:
            order_result: The order result to rollback
            close_side: Side to close ('buy' or 'sell')
        """
        try:
            self.logger.log(f"Rolling back GRVT position: {order_result.filled_size} @ {order_result.price}", "WARNING")

            if order_result.filled_size and order_result.filled_size > 0:
                await self.grvt_client.place_market_order(
                    self.config.contract_id,
                    order_result.filled_size,
                    close_side
                )
                self.logger.log("GRVT rollback completed", "INFO")

        except Exception as e:
            self.logger.log(f"Error during GRVT rollback: {e}", "ERROR")
            await self.send_notification(f"⚠️ CRITICAL: GRVT rollback failed: {e}")

    async def _check_stop_conditions(self) -> Tuple[bool, str]:
        """Check if stop-loss or take-profit conditions are met (using absolute USDC values).

        Returns:
            Tuple of (should_close, reason)
        """
        if not self.position.is_open:
            return False, ""

        try:
            # Get current prices
            grvt_bid, grvt_ask = await self.grvt_client.fetch_bbo_prices(self.config.contract_id)
            grvt_price = (Decimal(str(grvt_bid)) + Decimal(str(grvt_ask))) / Decimal('2')

            lighter_bid, lighter_ask = await self.lighter_client.fetch_bbo_prices(self.lighter_client.config.contract_id)
            lighter_price = (Decimal(str(lighter_bid)) + Decimal(str(lighter_ask))) / Decimal('2')

            # Calculate absolute P&L in USDC based on direction
            if self.config.reverse:
                # GRVT SHORT + Lighter LONG
                grvt_pnl_usdc = (self.position.grvt_entry_price - grvt_price) * self.position.grvt_quantity
                lighter_pnl_usdc = (lighter_price - self.position.lighter_entry_price) * self.position.lighter_quantity
            else:
                # GRVT LONG + Lighter SHORT
                grvt_pnl_usdc = (grvt_price - self.position.grvt_entry_price) * self.position.grvt_quantity
                lighter_pnl_usdc = (self.position.lighter_entry_price - lighter_price) * self.position.lighter_quantity

            # Calculate total P&L
            total_pnl_usdc = grvt_pnl_usdc + lighter_pnl_usdc

            # Calculate percentage P&L for logging
            grvt_pnl_pct = (grvt_pnl_usdc / (self.position.grvt_entry_price * self.position.grvt_quantity)) * Decimal('100')
            lighter_pnl_pct = (lighter_pnl_usdc / (self.position.lighter_entry_price * self.position.lighter_quantity)) * Decimal('100')

            self.logger.log(
                f"P&L: GRVT={grvt_pnl_pct:.2f}% ({grvt_pnl_usdc:+.2f} USDC), "
                f"Lighter={lighter_pnl_pct:.2f}% ({lighter_pnl_usdc:+.2f} USDC), "
                f"Total={total_pnl_usdc:+.2f} USDC",
                "INFO"
            )

            # Check stop loss (total P&L in USDC)
            if total_pnl_usdc <= -self.config.max_loss_usdc:
                return True, f"Stop Loss triggered: {total_pnl_usdc:.2f} USDC (threshold: -{self.config.max_loss_usdc} USDC)"

            # Check take profit (total P&L in USDC)
            if total_pnl_usdc >= self.config.max_profit_usdc:
                return True, f"Take Profit triggered: {total_pnl_usdc:.2f} USDC (threshold: +{self.config.max_profit_usdc} USDC)"

            return False, ""

        except Exception as e:
            self.logger.log(f"Error checking stop conditions: {e}", "ERROR")
            return False, ""

    async def _close_hedge_positions(self):
        """Close hedged positions - GRVT uses POST_ONLY limit order with dynamic price adjustment.

        Strategy:
        1. Place POST_ONLY limit close order on GRVT (maker, low fees)
        2. Wait 3 seconds for fill
        3. If not filled: cancel and retry with updated price (chase the market)
        4. Once GRVT filled, immediately close Lighter position with market order
        5. Maximum 20 retries before giving up
        """
        try:
            self.logger.log("=== Closing Cross-Exchange Hedge Positions ===", "INFO")

            if not self.position.is_open:
                self.logger.log("No open positions to close", "WARNING")
                return

            # Determine close sides based on reverse flag
            if self.config.reverse:
                grvt_close_side = 'buy'   # Close SHORT
                lighter_close_side = 'sell'  # Close LONG
            else:
                grvt_close_side = 'sell'  # Close LONG
                lighter_close_side = 'buy'   # Close SHORT

            # Dynamic retry loop for GRVT close order
            max_retries = 20
            retry_timeout = 3  # 3 seconds per attempt
            grvt_close = None
            filled = False

            for attempt in range(1, max_retries + 1):
                # Get fresh BBO prices for each attempt
                grvt_bid, grvt_ask = await self.grvt_client.fetch_bbo_prices(self.config.contract_id)

                # Calculate close price for POST_ONLY order (dynamic price adjustment)
                if grvt_close_side == 'sell':
                    # Selling: place slightly above best bid to ensure maker
                    close_price = grvt_bid + self.config.tick_size
                else:
                    # Buying: place slightly below best ask to ensure maker
                    close_price = grvt_ask - self.config.tick_size

                # Place POST_ONLY close order on GRVT
                self.logger.log(
                    f"Attempt {attempt}/{max_retries}: Placing GRVT {grvt_close_side.upper()} "
                    f"POST_ONLY @ {close_price} (bid={grvt_bid}, ask={grvt_ask})...",
                    "INFO"
                )

                try:
                    grvt_close = await self.grvt_client.place_close_order(
                        self.config.contract_id,
                        self.position.grvt_quantity,
                        close_price,
                        grvt_close_side
                    )
                except Exception as e:
                    self.logger.log(f"GRVT close order placement failed: {e}", "ERROR")
                    await asyncio.sleep(0.5)
                    continue

                if not grvt_close.success:
                    self.logger.log(f"GRVT close order rejected: {grvt_close.error_message}", "WARNING")
                    await asyncio.sleep(0.5)
                    continue

                # Wait for order to fill (3 seconds timeout)
                start_time = asyncio.get_event_loop().time()

                while asyncio.get_event_loop().time() - start_time < retry_timeout:
                    order_info = await self.grvt_client.get_order_info(order_id=grvt_close.order_id)

                    if order_info and order_info.status == 'FILLED' and order_info.filled_size > 0:
                        filled = True
                        grvt_close.filled_size = order_info.filled_size
                        grvt_close.price = order_info.price
                        self.logger.log(
                            f"✓ GRVT closed (POST_ONLY) on attempt {attempt}: "
                            f"{order_info.filled_size} @ {order_info.price}",
                            "INFO"
                        )
                        break

                    await asyncio.sleep(0.2)  # Check every 200ms

                if filled:
                    break

                # Not filled within 3 seconds - cancel and retry
                self.logger.log(
                    f"Order not filled within {retry_timeout}s, canceling and retrying...",
                    "INFO"
                )
                try:
                    await self.grvt_client.cancel_order(grvt_close.order_id)
                except Exception as e:
                    self.logger.log(f"Error canceling order: {e}", "WARNING")

            # Check if GRVT close succeeded
            if not filled:
                self.logger.log(
                    f"⚠️ GRVT close failed after {max_retries} attempts, position remains open",
                    "ERROR"
                )
                return

            # Step 2: Immediately close Lighter position with market order
            self.logger.log(f"Placing Lighter {lighter_close_side.upper()} market close order...", "INFO")
            try:
                lighter_close = await self.lighter_client.place_market_order(
                    self.lighter_client.config.contract_id,
                    self.position.lighter_quantity,
                    lighter_close_side
                )

                if lighter_close.success:
                    self.logger.log(f"✓ Lighter closed (market): {lighter_close.filled_size} @ {lighter_close.price}", "INFO")
                else:
                    self.logger.log(f"Lighter close failed: status={lighter_close.status}", "ERROR")
                    self.logger.log("⚠️ GRVT closed but Lighter still open - POSITION IMBALANCE", "ERROR")

            except Exception as e:
                self.logger.log(f"Lighter close failed: {e}", "ERROR")
                self.logger.log("⚠️ GRVT closed but Lighter still open - POSITION IMBALANCE", "ERROR")

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
        """Main trading loop - continuous hedge cycle with configurable interval."""
        try:
            # Initialize clients
            await self.initialize()

            # Log configuration
            self.logger.log("=== GRVT ↔ Lighter Cross-Exchange Hedge Bot Configuration ===", "INFO")
            self.logger.log(f"Ticker: {self.config.ticker}", "INFO")
            self.logger.log(f"GRVT Contract: {self.config.contract_id}", "INFO")
            self.logger.log(f"Lighter Contract: {self.lighter_client.config.contract_id}", "INFO")
            self.logger.log(f"Margin per trade: {self.config.margin} USDC", "INFO")
            self.logger.log(f"Hold time: {self.config.hold_time}s", "INFO")
            self.logger.log(f"Take Profit: {self.config.take_profit}%", "INFO")
            self.logger.log(f"Stop Loss: {self.config.stop_loss}%", "INFO")
            self.logger.log(f"Reverse mode: {self.config.reverse}", "INFO")
            self.logger.log(f"Cycle wait: {self.config.cycle_wait}s", "INFO")
            self.logger.log("=============================================================", "INFO")

            # Continuous trading loop
            while not self.shutdown_requested:
                try:
                    # Open hedge positions
                    success = await self._open_hedge_positions()

                    if not success:
                        self.logger.log(f"Failed to open positions, retrying in {self.config.cycle_wait} seconds...", "WARNING")
                        await asyncio.sleep(self.config.cycle_wait)
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

                    # Wait before next cycle
                    self.logger.log(f"Waiting {self.config.cycle_wait} seconds before next cycle...", "INFO")
                    await asyncio.sleep(self.config.cycle_wait)

                except Exception as e:
                    self.logger.log(f"Error in trading cycle: {e}", "ERROR")
                    self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
                    await asyncio.sleep(self.config.cycle_wait)

        except KeyboardInterrupt:
            self.logger.log("Bot stopped by user", "INFO")
        except Exception as e:
            self.logger.log(f"Critical error: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            await self.send_notification(f"⚠️ GRVT ↔ Lighter Hedge Bot Critical Error: {e}")
        finally:
            # Cleanup
            try:
                if self.position.is_open:
                    self.logger.log("Closing open positions before shutdown...", "INFO")
                    await self._close_hedge_positions()

                if self.grvt_client:
                    await self.grvt_client.disconnect()
                if self.lighter_client:
                    await self.lighter_client.disconnect()

                self.logger.log("GRVT ↔ Lighter hedge bot shutdown complete", "INFO")
            except Exception as e:
                self.logger.log(f"Error during shutdown: {e}", "ERROR")
