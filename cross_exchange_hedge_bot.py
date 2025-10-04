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
    take_profit: Decimal  # Take profit percentage (default 50%, deprecated - use max_profit_usdc)
    stop_loss: Decimal  # Stop loss percentage (default 50%, deprecated - use max_loss_usdc)
    max_loss_usdc: Decimal = Decimal('50')  # Maximum loss in USDC before stop loss
    max_profit_usdc: Decimal = Decimal('100')  # Maximum profit in USDC for take profit (optional)
    reverse: bool = False  # Reverse direction (Paradex SHORT + Lighter LONG)
    cycle_wait: int = 20  # Wait time between trading cycles in seconds
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
    emergency_close: bool = False  # Flag for emergency market order close (stop loss/take profit)


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

    async def _calculate_quantity_from_margin(self, target_price: Decimal, order_side: str) -> Decimal:
        """Calculate position quantity based on margin and Paradex's actual order price.

        This method ensures that the actual notional value on Paradex matches the target margin,
        accounting for order_size_increment precision truncation.

        Args:
            target_price: Paradex's actual order price (not average)
            order_side: 'buy' or 'sell' to handle precision rounding correctly

        Returns:
            Position quantity (adjusted for Paradex's order_size_increment precision)
        """
        # Ensure target_price is Decimal
        target_price = Decimal(str(target_price))

        # Calculate raw quantity based on target margin
        raw_quantity = self.config.margin / target_price

        # Get Paradex's order size increment (precision requirement)
        paradex_increment = self.paradex_client.order_size_increment

        # CRITICAL: Use ROUND_DOWN to ensure we don't exceed the margin budget
        # This prevents over-allocation and maintains accurate notional value
        from decimal import ROUND_DOWN
        adjusted_quantity = raw_quantity.quantize(paradex_increment, rounding=ROUND_DOWN)

        # Calculate actual notional value after precision truncation
        actual_notional = adjusted_quantity * target_price

        # Log detailed calculation for monitoring
        self.logger.log(
            f"Quantity calculation: margin={self.config.margin} USDC, price={target_price}, "
            f"raw_qty={raw_quantity:.8f}, adjusted_qty={adjusted_quantity}, "
            f"actual_notional={actual_notional:.2f} USDC, increment={paradex_increment}",
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
        Quantity is calculated using Paradex's actual order price to ensure accurate notional value.

        Returns:
            Average mid price (for reference only)
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
        """Open hedged positions - Paradex uses dynamic retry limit order, Lighter uses market order.

        Strategy:
        1. Place POST_ONLY limit order on Paradex (maker, low fees) with dynamic price tracking
        2. Wait 3 seconds for fill, if not filled: cancel and retry with updated price
        3. Infinite retries until filled (ensures opening position eventually)
        4. Once Paradex filled, immediately hedge with Lighter market order (taker)

        Returns:
            True if both positions opened successfully, False otherwise
        """
        try:
            self.logger.log("=== Opening Cross-Exchange Hedge Positions ===", "INFO")

            # Determine sides based on reverse flag
            if self.config.reverse:
                paradex_side = 'sell'  # Paradex SHORT
                lighter_side = 'buy'   # Lighter LONG
                mode_desc = "Reverse mode: Paradex SHORT (maker) + Lighter LONG (taker)"
            else:
                paradex_side = 'buy'   # Paradex LONG
                lighter_side = 'sell'  # Lighter SHORT
                mode_desc = "Normal mode: Paradex LONG (maker) + Lighter SHORT (taker)"

            self.logger.log(mode_desc, "INFO")

            # Dynamic retry loop for Paradex open order (infinite retries until filled)
            retry_timeout = 3  # 3 seconds per attempt
            paradex_result = None
            filled = False
            attempt = 0

            while not filled and not self.shutdown_requested:
                attempt += 1

                # Get fresh BBO prices for each attempt
                paradex_bid, paradex_ask = await self.paradex_client.fetch_bbo_prices(self.config.contract_id)

                # Calculate Paradex's actual order price (same logic as paradex.py:get_order_price)
                if paradex_side == 'buy':
                    # For buy orders, place slightly below best ask to ensure maker
                    paradex_order_price = paradex_ask - self.config.tick_size
                else:
                    # For sell orders, place slightly above best bid to ensure maker
                    paradex_order_price = paradex_bid + self.config.tick_size

                # Round to tick size
                paradex_order_price = self.paradex_client.round_to_tick(paradex_order_price)

                # Validate price
                if paradex_order_price <= 0:
                    self.logger.log(f"Invalid Paradex order price: {paradex_order_price}, retrying...", "WARNING")
                    await asyncio.sleep(0.5)
                    continue

                # Calculate quantity based on Paradex's actual order price (recalculate each time for accuracy)
                quantity = await self._calculate_quantity_from_margin(paradex_order_price, paradex_side)

                # Log attempt
                self.logger.log(
                    f"Attempt {attempt}: Placing Paradex {paradex_side.upper()} POST_ONLY @ {paradex_order_price} "
                    f"(bid={paradex_bid}, ask={paradex_ask}, qty={quantity})",
                    "INFO"
                )

                # Place POST_ONLY limit order on Paradex
                try:
                    paradex_result = await self.paradex_client.place_open_order(
                        self.config.contract_id, quantity, paradex_side
                    )
                except Exception as e:
                    self.logger.log(f"Paradex order placement failed: {e}, retrying...", "ERROR")
                    await asyncio.sleep(0.5)
                    continue

                if not paradex_result.success:
                    self.logger.log(f"Paradex order rejected: {paradex_result.error_message}, retrying...", "WARNING")
                    await asyncio.sleep(0.5)
                    continue

                # Wait for order to fill (3 seconds timeout)
                start_time = asyncio.get_event_loop().time()

                while asyncio.get_event_loop().time() - start_time < retry_timeout:
                    order_info = await self.paradex_client.get_order_info(paradex_result.order_id)

                    if order_info and order_info.status == 'CLOSED' and order_info.filled_size > 0:
                        filled = True
                        paradex_result.filled_size = order_info.filled_size
                        paradex_result.price = order_info.price
                        self.logger.log(
                            f"✓ Paradex open order filled on attempt {attempt}: "
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
                    await self.paradex_client.cancel_order(paradex_result.order_id)
                except Exception as e:
                    self.logger.log(f"Error canceling order: {e}", "WARNING")

            # Check if we were interrupted
            if self.shutdown_requested:
                self.logger.log("Opening positions interrupted by shutdown request", "WARNING")
                return False

            # Step 2: Immediately place Lighter market order to hedge
            self.logger.log(f"Placing Lighter {lighter_side.upper()} market order to hedge...", "INFO")
            try:
                lighter_result = await self.lighter_client.place_market_order(
                    self.lighter_client.config.contract_id,
                    paradex_result.filled_size,  # Use actual filled size from Paradex
                    lighter_side
                )
            except Exception as e:
                self.logger.log(f"Lighter hedge failed: {e}", "ERROR")
                # Rollback Paradex position
                await self._rollback_paradex_position(paradex_result, 'sell' if paradex_side == 'buy' else 'buy')
                return False

            # Check if Lighter order succeeded
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
            self.position.paradex_quantity = paradex_result.filled_size
            self.position.lighter_quantity = lighter_result.filled_size or paradex_result.filled_size
            self.position.entry_time = asyncio.get_event_loop().time()
            self.position.is_open = True

            # ========== Calculate and verify actual notional values ==========
            paradex_notional = self.position.paradex_quantity * self.position.paradex_entry_price
            lighter_notional = self.position.lighter_quantity * self.position.lighter_entry_price
            target_margin = self.config.margin

            # Calculate deviations
            paradex_deviation_pct = abs(paradex_notional - target_margin) / target_margin * Decimal('100')
            lighter_deviation_pct = abs(lighter_notional - target_margin) / target_margin * Decimal('100')

            self.logger.log(f"✓ Paradex {paradex_side.upper()} (maker): {self.position.paradex_quantity} @ {self.position.paradex_entry_price}", "INFO")
            self.logger.log(f"✓ Lighter {lighter_side.upper()} (taker): {self.position.lighter_quantity} @ {self.position.lighter_entry_price}", "INFO")

            # Log notional values with color-coded warnings
            self.logger.log(
                f"📊 Notional values - Target: {target_margin:.2f} USDC | "
                f"Paradex: {paradex_notional:.2f} USDC ({paradex_deviation_pct:+.2f}%) | "
                f"Lighter: {lighter_notional:.2f} USDC ({lighter_deviation_pct:+.2f}%)",
                "INFO"
            )

            # Warn if deviations are significant
            if paradex_deviation_pct > Decimal('15'):
                self.logger.log(
                    f"⚠️ Paradex notional deviation: {paradex_deviation_pct:.2f}% "
                    f"(actual: {paradex_notional:.2f}, target: {target_margin:.2f})",
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

    async def _emergency_market_close(self, paradex_close_side: str, lighter_close_side: str):
        """Emergency close using market orders on both exchanges (for stop loss/take profit).

        Args:
            paradex_close_side: Paradex close side ('buy' or 'sell')
            lighter_close_side: Lighter close side ('buy' or 'sell')
        """
        try:
            self.logger.log("⚡ Executing EMERGENCY MARKET CLOSE on both exchanges", "WARNING")

            # Close both positions simultaneously with market orders
            paradex_task = self.paradex_client.place_market_order(
                self.config.contract_id,
                self.position.paradex_quantity,
                paradex_close_side
            )

            lighter_task = self.lighter_client.place_market_order(
                self.lighter_client.config.contract_id,
                self.position.lighter_quantity,
                lighter_close_side
            )

            # Execute both market orders in parallel
            paradex_result, lighter_result = await asyncio.gather(paradex_task, lighter_task, return_exceptions=True)

            # Check Paradex result
            if isinstance(paradex_result, Exception):
                self.logger.log(f"❌ Paradex market close failed: {paradex_result}", "ERROR")
                await self.send_notification(f"🚨 CRITICAL: Paradex emergency close failed: {paradex_result}")
            elif paradex_result.success and paradex_result.status == 'FILLED':
                self.logger.log(f"✓ Paradex closed (market): {paradex_result.filled_size} @ {paradex_result.price}", "INFO")
            else:
                self.logger.log(f"❌ Paradex market close unsuccessful: status={paradex_result.status}", "ERROR")
                await self.send_notification(f"🚨 CRITICAL: Paradex emergency close status: {paradex_result.status}")

            # Check Lighter result
            if isinstance(lighter_result, Exception):
                self.logger.log(f"❌ Lighter market close failed: {lighter_result}", "ERROR")
                await self.send_notification(f"🚨 CRITICAL: Lighter emergency close failed: {lighter_result}")
            elif lighter_result.success and lighter_result.status == 'FILLED':
                self.logger.log(f"✓ Lighter closed (market): {lighter_result.filled_size} @ {lighter_result.price}", "INFO")
            else:
                self.logger.log(f"❌ Lighter market close unsuccessful: status={lighter_result.status}", "ERROR")
                await self.send_notification(f"🚨 CRITICAL: Lighter emergency close status: {lighter_result.status}")

            # Reset position state
            self.position = CrossPositionState()

            self.logger.log("=== EMERGENCY CLOSE COMPLETED ===", "INFO")

        except Exception as e:
            self.logger.log(f"❌ Critical error during emergency close: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            await self.send_notification(f"🚨 CRITICAL: Emergency close exception: {e}")

    async def _check_stop_conditions(self) -> Tuple[bool, str]:
        """Check if stop-loss or take-profit conditions are met (using absolute USDC values).

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

            # Calculate absolute P&L in USDC based on direction
            if self.config.reverse:
                # Paradex SHORT + Lighter LONG
                paradex_pnl_usdc = (self.position.paradex_entry_price - paradex_price) * self.position.paradex_quantity
                lighter_pnl_usdc = (lighter_price - self.position.lighter_entry_price) * self.position.lighter_quantity
            else:
                # Paradex LONG + Lighter SHORT
                paradex_pnl_usdc = (paradex_price - self.position.paradex_entry_price) * self.position.paradex_quantity
                lighter_pnl_usdc = (self.position.lighter_entry_price - lighter_price) * self.position.lighter_quantity

            # Calculate total P&L
            total_pnl_usdc = paradex_pnl_usdc + lighter_pnl_usdc

            # Calculate percentage P&L for logging
            paradex_pnl_pct = (paradex_pnl_usdc / (self.position.paradex_entry_price * self.position.paradex_quantity)) * Decimal('100')
            lighter_pnl_pct = (lighter_pnl_usdc / (self.position.lighter_entry_price * self.position.lighter_quantity)) * Decimal('100')

            self.logger.log(
                f"P&L: Paradex={paradex_pnl_pct:.2f}% ({paradex_pnl_usdc:+.2f} USDC), "
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
        """Close hedged positions - uses market orders for emergency close (stop loss/take profit).

        Strategy:
        - Normal close (hold time expired): POST_ONLY limit order with 30 retries + market fallback
        - Emergency close (stop loss/take profit): Market orders for immediate execution
        """
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

            # Check if emergency close (stop loss/take profit triggered)
            if self.position.emergency_close:
                self.logger.log("🚨 EMERGENCY CLOSE: Using market orders for immediate execution", "WARNING")
                await self._emergency_market_close(paradex_close_side, lighter_close_side)
                return

            # Dynamic retry loop for Paradex close order
            max_retries = 30  # Increased to 30 retries before fallback to market order
            retry_timeout = 3  # 3 seconds per attempt
            paradex_close = None
            filled = False

            for attempt in range(1, max_retries + 1):
                # Get fresh BBO prices for each attempt
                paradex_bid, paradex_ask = await self.paradex_client.fetch_bbo_prices(self.config.contract_id)

                # Calculate close price for POST_ONLY order (dynamic price adjustment)
                if paradex_close_side == 'sell':
                    # Selling: place slightly above best bid to ensure maker
                    close_price = paradex_bid + self.config.tick_size
                else:
                    # Buying: place slightly below best ask to ensure maker
                    close_price = paradex_ask - self.config.tick_size

                # Place POST_ONLY close order on Paradex
                self.logger.log(
                    f"Attempt {attempt}/{max_retries}: Placing Paradex {paradex_close_side.upper()} "
                    f"POST_ONLY @ {close_price} (bid={paradex_bid}, ask={paradex_ask})...",
                    "INFO"
                )

                try:
                    paradex_close = await self.paradex_client.place_close_order(
                        self.config.contract_id,
                        self.position.paradex_quantity,
                        close_price,
                        paradex_close_side
                    )
                except Exception as e:
                    self.logger.log(f"Paradex close order placement failed: {e}", "ERROR")
                    await asyncio.sleep(0.5)
                    continue

                if not paradex_close.success:
                    self.logger.log(f"Paradex close order rejected: {paradex_close.error_message}", "WARNING")
                    await asyncio.sleep(0.5)
                    continue

                # Wait for order to fill (3 seconds timeout)
                start_time = asyncio.get_event_loop().time()

                while asyncio.get_event_loop().time() - start_time < retry_timeout:
                    order_info = await self.paradex_client.get_order_info(order_id=paradex_close.order_id)

                    if order_info and order_info.status == 'CLOSED' and order_info.filled_size > 0:
                        filled = True
                        paradex_close.filled_size = order_info.filled_size
                        paradex_close.price = order_info.price
                        self.logger.log(
                            f"✓ Paradex closed (POST_ONLY) on attempt {attempt}: "
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
                    await self.paradex_client.cancel_order(paradex_close.order_id)
                except Exception as e:
                    self.logger.log(f"Error canceling order: {e}", "WARNING")

            # Check if Paradex close succeeded
            if not filled:
                # Fallback to emergency market order after max retries
                self.logger.log(
                    f"⚠️ POST_ONLY limit order failed after {max_retries} attempts ({max_retries * retry_timeout}s)",
                    "ERROR"
                )
                self.logger.log("🚨 FALLBACK: Executing emergency market order to close position", "WARNING")
                await self.send_notification(
                    f"⚠️ Paradex limit order failed after {max_retries} attempts, "
                    f"falling back to market order"
                )

                # Use emergency market close as fallback
                await self._emergency_market_close(paradex_close_side, lighter_close_side)
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
                    self.logger.log("⚠️ Paradex closed but Lighter still open - POSITION IMBALANCE", "ERROR")

            except Exception as e:
                self.logger.log(f"Lighter close failed: {e}", "ERROR")
                self.logger.log("⚠️ Paradex closed but Lighter still open - POSITION IMBALANCE", "ERROR")

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
                            # Set emergency close flag for market order execution
                            self.position.emergency_close = True
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
