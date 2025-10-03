#!/usr/bin/env python3
"""
GRVT ↔ Lighter Cross-Exchange Hedge Bot - Entry point
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from decimal import Decimal
import dotenv

from grvt_lighter_hedge_bot import GrvtLighterHedgeBot, CrossHedgeConfig


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='GRVT ↔ Lighter Cross-Exchange Hedge Bot - Hedging for Volume Generation'
    )

    # Trading parameters
    parser.add_argument('--ticker', type=str, default='BTC',
                        help='Ticker symbol (default: BTC)')
    parser.add_argument('--margin', type=Decimal, required=False,
                        help='Margin per trade in USDC (if not set, uses CROSS_HEDGE_MARGIN from env)')
    parser.add_argument('--hold-time', type=int, required=False,
                        help='Position hold time in seconds (if not set, uses CROSS_HEDGE_POSITION_HOLD_TIME from env)')
    parser.add_argument('--take-profit', type=Decimal, required=False,
                        help='Take profit percentage (if not set, uses CROSS_HEDGE_TAKE_PROFIT from env, default 50)')
    parser.add_argument('--stop-loss', type=Decimal, required=False,
                        help='Stop loss percentage (if not set, uses CROSS_HEDGE_STOP_LOSS from env, default 50)')
    parser.add_argument('--env-file', type=str, default=".env",
                        help=".env file path (default: .env)")

    return parser.parse_args()


def setup_logging(log_level: str):
    """Setup global logging configuration."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    root_logger.setLevel(level)

    # Suppress noisy loggers
    logging.getLogger('websockets').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('lighter').setLevel(logging.WARNING)


def validate_env_variables():
    """Validate required environment variables."""
    required_vars = [
        # GRVT Configuration
        'GRVT_TRADING_ACCOUNT_ID',
        'GRVT_PRIVATE_KEY',
        'GRVT_API_KEY',
        # Lighter Configuration
        'API_KEY_PRIVATE_KEY',
        'LIGHTER_ACCOUNT_INDEX',
        'LIGHTER_API_KEY_INDEX',
    ]

    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        print(f"Error: Missing required environment variables: {', '.join(missing_vars)}")
        print("\nPlease ensure your .env file contains:")
        print("  # GRVT Configuration")
        print("  GRVT_TRADING_ACCOUNT_ID=<your_trading_account_id>")
        print("  GRVT_PRIVATE_KEY=<your_private_key>")
        print("  GRVT_API_KEY=<your_api_key>")
        print("  GRVT_ENVIRONMENT=prod  # or testnet")
        print("\n  # Lighter Configuration")
        print("  API_KEY_PRIVATE_KEY=<your_api_key_private_key>")
        print("  LIGHTER_ACCOUNT_INDEX=<your_account_index>")
        print("  LIGHTER_API_KEY_INDEX=<your_api_key_index>")
        print("\n  # Cross-Exchange Hedge Bot Configuration")
        print("  CROSS_HEDGE_MARGIN=<margin_in_usdc>")
        print("  CROSS_HEDGE_POSITION_HOLD_TIME=<hold_time_seconds>")
        print("  CROSS_HEDGE_TAKE_PROFIT=<take_profit_percentage> (optional, default 50)")
        print("  CROSS_HEDGE_STOP_LOSS=<stop_loss_percentage> (optional, default 50)")
        print("  CROSS_HEDGE_REVERSE=false (optional, default false)")
        print("  CROSS_HEDGE_CYCLE_WAIT=20 (optional, default 20)")
        sys.exit(1)


async def main():
    """Main entry point."""
    args = parse_arguments()

    # Setup logging
    setup_logging("WARNING")

    # Load environment file
    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"Error: Env file not found: {env_path.resolve()}")
        sys.exit(1)

    dotenv.load_dotenv(args.env_file)

    # Validate environment variables
    validate_env_variables()

    # Get configuration from args or environment
    margin = args.margin if args.margin else Decimal(os.getenv('CROSS_HEDGE_MARGIN', '100'))
    hold_time = args.hold_time if args.hold_time else int(os.getenv('CROSS_HEDGE_POSITION_HOLD_TIME', '300'))
    take_profit = args.take_profit if args.take_profit else Decimal(os.getenv('CROSS_HEDGE_TAKE_PROFIT', '50'))
    stop_loss = args.stop_loss if args.stop_loss else Decimal(os.getenv('CROSS_HEDGE_STOP_LOSS', '50'))
    reverse = os.getenv('CROSS_HEDGE_REVERSE', 'false').lower() == 'true'
    cycle_wait = int(os.getenv('CROSS_HEDGE_CYCLE_WAIT', '20'))

    # Create configuration
    config = CrossHedgeConfig(
        ticker=args.ticker.upper(),
        margin=margin,
        hold_time=hold_time,
        take_profit=take_profit,
        stop_loss=stop_loss,
        reverse=reverse,
        cycle_wait=cycle_wait
    )

    # Create and run the bot
    bot = GrvtLighterHedgeBot(config)
    try:
        await bot.run()
    except Exception as e:
        print(f"Bot execution failed: {e}")
        import traceback
        traceback.print_exc()
        return


if __name__ == "__main__":
    asyncio.run(main())
