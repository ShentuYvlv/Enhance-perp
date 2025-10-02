#!/usr/bin/env python3
"""
Quick test script for Hedge Volume Bot
This script performs basic validation without actual trading
"""

import os
import sys
from pathlib import Path
import dotenv


def check_env_variables():
    """Check if all required environment variables are set."""
    print("=" * 60)
    print("Hedge Bot Configuration Checker")
    print("=" * 60)

    required_vars = [
        ('API_KEY_PRIVATE_KEY_1', 'Account 1 Private Key'),
        ('LIGHTER_ACCOUNT_INDEX_1', 'Account 1 Index'),
        ('LIGHTER_API_KEY_INDEX_1', 'Account 1 API Key Index'),
        ('API_KEY_PRIVATE_KEY_2', 'Account 2 Private Key'),
        ('LIGHTER_ACCOUNT_INDEX_2', 'Account 2 Index'),
        ('LIGHTER_API_KEY_INDEX_2', 'Account 2 API Key Index'),
    ]

    optional_vars = [
        ('HEDGE_MARGIN', 'Margin per trade (USDC)', '100'),
        ('HEDGE_POSITION_HOLD_TIME', 'Position hold time (seconds)', '300'),
        ('HEDGE_TAKE_PROFIT', 'Take profit percentage', '50'),
        ('HEDGE_STOP_LOSS', 'Stop loss percentage', '50'),
        ('TELEGRAM_BOT_TOKEN', 'Telegram bot token', 'Not set'),
        ('TELEGRAM_CHAT_ID', 'Telegram chat ID', 'Not set'),
        ('LARK_TOKEN', 'Lark token', 'Not set'),
    ]

    all_ok = True

    print("\n✓ Required Variables:")
    for var_name, description in required_vars:
        value = os.getenv(var_name)
        if value:
            # Mask sensitive data
            if 'KEY' in var_name or 'PRIVATE' in var_name:
                display_value = value[:8] + '...' + value[-8:] if len(value) > 16 else '***'
            else:
                display_value = value
            print(f"  ✓ {description:30s}: {display_value}")
        else:
            print(f"  ✗ {description:30s}: MISSING")
            all_ok = False

    print("\n✓ Optional Variables (will use defaults if not set):")
    for var_name, description, default in optional_vars:
        value = os.getenv(var_name)
        if value:
            if 'TOKEN' in var_name:
                display_value = value[:8] + '...' if len(value) > 8 else '***'
            else:
                display_value = value
            print(f"  ✓ {description:30s}: {display_value}")
        else:
            print(f"  - {description:30s}: {default} (default)")

    print("\n" + "=" * 60)

    if all_ok:
        print("✓ All required variables are set!")
        print("\nYou can now run the hedge bot with:")
        print("  python run_hedge_bot.py --ticker BTC")
        print("\nOr with custom parameters:")
        print("  python run_hedge_bot.py --ticker ETH --margin 200 --hold-time 600")
        return True
    else:
        print("✗ Some required variables are missing!")
        print("\nPlease update your .env file with the missing variables.")
        print("See env_example.txt for reference.")
        return False


def check_imports():
    """Check if all required modules can be imported."""
    print("\n" + "=" * 60)
    print("Checking Python Dependencies")
    print("=" * 60)

    required_modules = [
        ('asyncio', 'Python standard library'),
        ('decimal', 'Python standard library'),
        ('dataclasses', 'Python standard library'),
        ('dotenv', 'python-dotenv package'),
        ('lighter', 'lighter-python SDK'),
        ('aiohttp', 'aiohttp package'),
    ]

    all_ok = True

    for module_name, description in required_modules:
        try:
            __import__(module_name)
            print(f"  ✓ {module_name:20s}: OK ({description})")
        except ImportError:
            print(f"  ✗ {module_name:20s}: MISSING ({description})")
            all_ok = False

    print("=" * 60)

    if all_ok:
        print("✓ All required modules are installed!")
        return True
    else:
        print("✗ Some required modules are missing!")
        print("\nPlease install missing dependencies:")
        print("  pip install -r requirements.txt")
        return False


def check_files():
    """Check if all required files exist."""
    print("\n" + "=" * 60)
    print("Checking Project Files")
    print("=" * 60)

    required_files = [
        'hedge_volume_bot.py',
        'run_hedge_bot.py',
        'exchanges/lighter.py',
        'helpers/logger.py',
    ]

    all_ok = True

    for file_path in required_files:
        full_path = Path(file_path)
        if full_path.exists():
            print(f"  ✓ {file_path}")
        else:
            print(f"  ✗ {file_path} - MISSING")
            all_ok = False

    print("=" * 60)

    if all_ok:
        print("✓ All required files are present!")
        return True
    else:
        print("✗ Some required files are missing!")
        return False


def main():
    """Main test function."""
    # Load environment variables
    env_file = sys.argv[1] if len(sys.argv) > 1 else '.env'
    env_path = Path(env_file)

    if not env_path.exists():
        print(f"Error: Environment file not found: {env_path.resolve()}")
        print(f"\nUsage: python test_hedge_bot.py [env_file]")
        print(f"Example: python test_hedge_bot.py .env")
        sys.exit(1)

    print(f"Loading environment from: {env_path.resolve()}")
    dotenv.load_dotenv(env_file)

    # Run checks
    files_ok = check_files()
    imports_ok = check_imports()
    env_ok = check_env_variables()

    # Final summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"Files:        {'✓ PASS' if files_ok else '✗ FAIL'}")
    print(f"Dependencies: {'✓ PASS' if imports_ok else '✗ FAIL'}")
    print(f"Environment:  {'✓ PASS' if env_ok else '✗ FAIL'}")
    print("=" * 60)

    if files_ok and imports_ok and env_ok:
        print("\n🎉 All checks passed! You're ready to run the hedge bot.")
        sys.exit(0)
    else:
        print("\n❌ Some checks failed. Please fix the issues above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
