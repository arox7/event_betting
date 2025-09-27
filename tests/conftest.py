import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables from project .env so tests pick up demo credentials
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
    logger.info("Loaded .env from %s", ENV_PATH)
else:
    load_dotenv()
    logger.info("Loaded .env from default search path")


pytest_plugins = ["pytest_asyncio"]


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark test to run on asyncio event loop")
    """Ensure demo-mode credentials are set before tests run."""
    os.environ.setdefault("KALSHI_DEMO_MODE", "true")

    demo_key = os.getenv("KALSHI_DEMO_API_KEY") or os.getenv("KALSHI_API_KEY_ID")
    if demo_key:
        os.environ["KALSHI_API_KEY_ID"] = demo_key
        logger.info("Using Kalshi API key id from environment")
    else:
        logger.warning("No Kalshi API key found; tests may not place real orders")

    demo_key_path = os.getenv("KALSHI_DEMO_PRIVATE_KEY_PATH") or os.getenv("KALSHI_PRIVATE_KEY_PATH")
    if demo_key_path:
        os.environ["KALSHI_PRIVATE_KEY_PATH"] = demo_key_path
        logger.info("Using Kalshi private key path from environment")
    else:
        logger.warning("No Kalshi private key path found; authentication will fail")

    os.environ.setdefault("KALSHI_DEMO_HOST", "https://demo-api.kalshi.co/trade-api/v2")
    os.environ.setdefault("KALSHI_API_HOST", os.environ["KALSHI_DEMO_HOST"])
