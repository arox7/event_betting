"""
Configuration system for the Kalshi Market Making Bot.

This module provides configuration management for the market making bot,
including parameter validation, environment variable support, and
configuration presets for different trading strategies.
"""

import os
import json
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from enum import Enum
from dotenv import load_dotenv
try:
    import yaml
except ImportError:
    yaml = None

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

class TradingMode(Enum):
    """Trading mode enumeration."""
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"
    CUSTOM = "custom"

class MarketSide(Enum):
    """Market side enumeration."""
    YES = "yes"
    NO = "no"
    BOTH = "both"

@dataclass
class RiskLimits:
    """Risk management limits."""
    max_position_per_market: int = 10
    max_total_exposure: int = 100
    max_order_size: int = 5
    max_daily_loss: float = 50.0
    stop_loss_percentage: float = 0.05
    emergency_stop_loss: float = 0.10
    max_markets: int = 10
    
    def __post_init__(self):
        """Validate risk limits."""
        if self.max_position_per_market <= 0:
            raise ValueError("max_position_per_market must be positive")
        if self.max_total_exposure <= 0:
            raise ValueError("max_total_exposure must be positive")
        if self.max_order_size <= 0:
            raise ValueError("max_order_size must be positive")
        if self.max_daily_loss <= 0:
            raise ValueError("max_daily_loss must be positive")
        if not 0 < self.stop_loss_percentage < 1:
            raise ValueError("stop_loss_percentage must be between 0 and 1")
        if not 0 < self.emergency_stop_loss < 1:
            raise ValueError("emergency_stop_loss must be between 0 and 1")

@dataclass
class MarketSelection:
    """Market selection criteria."""
    min_volume: int = 1000
    min_volume_24h: int = 500  # Minimum 24h volume for recent activity
    max_spread_cents: int = 5
    min_liquidity_dollars: float = 100.0
    min_open_interest: int = 1000  # Minimum open interest for market depth
    max_time_to_close_days: int = 30
    min_time_to_close_days: int = 1
    categories: List[str] = field(default_factory=list)
    exclude_categories: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        """Validate market selection criteria."""
        if self.min_volume < 0:
            raise ValueError("min_volume must be non-negative")
        if self.min_volume_24h < 0:
            raise ValueError("min_volume_24h must be non-negative")
        if self.max_spread_cents < 0:
            raise ValueError("max_spread_cents must be non-negative")
        if self.min_liquidity_dollars < 0:
            raise ValueError("min_liquidity_dollars must be non-negative")
        if self.min_open_interest < 0:
            raise ValueError("min_open_interest must be non-negative")
        if self.max_time_to_close_days < 0:
            raise ValueError("max_time_to_close_days must be non-negative")
        if self.min_time_to_close_days < 0:
            raise ValueError("min_time_to_close_days must be non-negative")

@dataclass
class PricingStrategy:
    """Pricing strategy configuration."""
    default_spread_cents: int = 2
    min_spread_cents: int = 1
    max_spread_cents: int = 10
    price_adjustment_factor: float = 0.5
    volatility_adjustment: bool = True
    momentum_adjustment: bool = True
    time_decay_adjustment: bool = True
    
    def __post_init__(self):
        """Validate pricing strategy."""
        if self.default_spread_cents < 0:
            raise ValueError("default_spread_cents must be non-negative")
        if self.min_spread_cents < 0:
            raise ValueError("min_spread_cents must be non-negative")
        if self.max_spread_cents < 0:
            raise ValueError("max_spread_cents must be non-negative")
        if self.min_spread_cents > self.max_spread_cents:
            raise ValueError("min_spread_cents cannot be greater than max_spread_cents")
        if self.default_spread_cents < self.min_spread_cents or self.default_spread_cents > self.max_spread_cents:
            raise ValueError("default_spread_cents must be between min and max")

@dataclass
class OrderManagement:
    """Order management configuration."""
    order_refresh_interval: int = 30
    max_orders_per_market: int = 2
    order_timeout_minutes: int = 60
    batch_order_size: int = 5
    order_retry_attempts: int = 3
    order_retry_delay: int = 5
    
    def __post_init__(self):
        """Validate order management settings."""
        if self.order_refresh_interval <= 0:
            raise ValueError("order_refresh_interval must be positive")
        if self.max_orders_per_market <= 0:
            raise ValueError("max_orders_per_market must be positive")
        if self.order_timeout_minutes <= 0:
            raise ValueError("order_timeout_minutes must be positive")
        if self.batch_order_size <= 0:
            raise ValueError("batch_order_size must be positive")
        if self.order_retry_attempts < 0:
            raise ValueError("order_retry_attempts must be non-negative")
        if self.order_retry_delay < 0:
            raise ValueError("order_retry_delay must be non-negative")

@dataclass
class MarketMakingConfig:
    """Complete market making bot configuration."""
    # Core settings
    trading_mode: TradingMode = TradingMode.MODERATE
    market_side: MarketSide = MarketSide.YES
    enabled: bool = True
    
    # Component configurations
    risk_limits: RiskLimits = field(default_factory=RiskLimits)
    market_selection: MarketSelection = field(default_factory=MarketSelection)
    pricing_strategy: PricingStrategy = field(default_factory=PricingStrategy)
    order_management: OrderManagement = field(default_factory=OrderManagement)
    
    # Advanced settings
    use_websockets: bool = True
    log_level: str = "INFO"
    performance_tracking: bool = True
    emergency_stop_enabled: bool = True
    
    def __post_init__(self):
        """Validate complete configuration."""
        # Validate that all components are valid
        self.risk_limits.__post_init__()
        self.market_selection.__post_init__()
        self.pricing_strategy.__post_init__()
        self.order_management.__post_init__()
        
        # Validate log level
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level.upper() not in valid_log_levels:
            raise ValueError(f"log_level must be one of {valid_log_levels}")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MarketMakingConfig':
        """Create configuration from dictionary."""
        # Handle enum conversions
        if 'trading_mode' in data and isinstance(data['trading_mode'], str):
            data['trading_mode'] = TradingMode(data['trading_mode'])
        if 'market_side' in data and isinstance(data['market_side'], str):
            data['market_side'] = MarketSide(data['market_side'])
        
        # Create nested objects
        if 'risk_limits' in data:
            data['risk_limits'] = RiskLimits(**data['risk_limits'])
        if 'market_selection' in data:
            data['market_selection'] = MarketSelection(**data['market_selection'])
        if 'pricing_strategy' in data:
            data['pricing_strategy'] = PricingStrategy(**data['pricing_strategy'])
        if 'order_management' in data:
            data['order_management'] = OrderManagement(**data['order_management'])
        
        return cls(**data)

class BotConfigManager:
    """Configuration manager for the market making bot."""
    
    def __init__(self, config_file: Optional[str] = None):
        """Initialize configuration manager."""
        self.config_file = config_file or os.getenv('BOT_CONFIG_FILE', 'bot_config.yaml')
        self._config: Optional[MarketMakingConfig] = None
    
    def load_config(self) -> MarketMakingConfig:
        """Load configuration from file or environment variables."""
        if self._config is not None:
            return self._config
        
        # Try to load from file first
        if os.path.exists(self.config_file):
            try:
                self._config = self._load_from_file()
                logger.info(f"Loaded configuration from {self.config_file}")
            except Exception as e:
                logger.warning(f"Failed to load config from file: {e}")
                self._config = self._load_from_environment()
        else:
            # Load from environment variables
            self._config = self._load_from_environment()
            logger.info("Loaded configuration from environment variables")
        
        return self._config
    
    def _load_from_file(self) -> MarketMakingConfig:
        """Load configuration from file."""
        with open(self.config_file, 'r') as f:
            if self.config_file.endswith('.json'):
                data = json.load(f)
            elif self.config_file.endswith('.yaml') or self.config_file.endswith('.yml'):
                if yaml is None:
                    raise ImportError("PyYAML is required for YAML config files. Install with: pip install PyYAML")
                data = yaml.safe_load(f)
            else:
                raise ValueError(f"Unsupported config file format: {self.config_file}")
        
        return MarketMakingConfig.from_dict(data)
    
    def _load_from_environment(self) -> MarketMakingConfig:
        """Load configuration from environment variables."""
        # Get trading mode
        trading_mode = TradingMode(os.getenv('BOT_TRADING_MODE', 'moderate'))
        
        # Get market side
        market_side = MarketSide(os.getenv('BOT_MARKET_SIDE', 'yes'))
        
        # Risk limits
        risk_limits = RiskLimits(
            max_position_per_market=int(os.getenv('BOT_MAX_POSITION_PER_MARKET', '10')),
            max_total_exposure=int(os.getenv('BOT_MAX_TOTAL_EXPOSURE', '100')),
            max_order_size=int(os.getenv('BOT_MAX_ORDER_SIZE', '5')),
            max_daily_loss=float(os.getenv('BOT_MAX_DAILY_LOSS', '50.0')),
            stop_loss_percentage=float(os.getenv('BOT_STOP_LOSS_PERCENTAGE', '0.05')),
            emergency_stop_loss=float(os.getenv('BOT_EMERGENCY_STOP_LOSS', '0.10')),
            max_markets=int(os.getenv('BOT_MAX_MARKETS', '10'))
        )
        
        # Market selection
        market_selection = MarketSelection(
            min_volume=int(os.getenv('BOT_MIN_VOLUME', '1000')),
            min_volume_24h=int(os.getenv('BOT_MIN_VOLUME_24H', '500')),
            max_spread_cents=int(os.getenv('BOT_MAX_SPREAD_CENTS', '5')),
            min_liquidity_dollars=float(os.getenv('BOT_MIN_LIQUIDITY_DOLLARS', '100.0')),
            min_open_interest=int(os.getenv('BOT_MIN_OPEN_INTEREST', '1000')),
            max_time_to_close_days=int(os.getenv('BOT_MAX_TIME_TO_CLOSE_DAYS', '30')),
            min_time_to_close_days=int(os.getenv('BOT_MIN_TIME_TO_CLOSE_DAYS', '1')),
            categories=os.getenv('BOT_CATEGORIES', '').split(',') if os.getenv('BOT_CATEGORIES') else [],
            exclude_categories=os.getenv('BOT_EXCLUDE_CATEGORIES', '').split(',') if os.getenv('BOT_EXCLUDE_CATEGORIES') else []
        )
        
        # Pricing strategy
        pricing_strategy = PricingStrategy(
            default_spread_cents=int(os.getenv('BOT_DEFAULT_SPREAD_CENTS', '2')),
            min_spread_cents=int(os.getenv('BOT_MIN_SPREAD_CENTS', '1')),
            max_spread_cents=int(os.getenv('BOT_MAX_SPREAD_CENTS', '10')),
            price_adjustment_factor=float(os.getenv('BOT_PRICE_ADJUSTMENT_FACTOR', '0.5')),
            volatility_adjustment=os.getenv('BOT_VOLATILITY_ADJUSTMENT', 'true').lower() == 'true',
            momentum_adjustment=os.getenv('BOT_MOMENTUM_ADJUSTMENT', 'true').lower() == 'true',
            time_decay_adjustment=os.getenv('BOT_TIME_DECAY_ADJUSTMENT', 'true').lower() == 'true'
        )
        
        # Order management
        order_management = OrderManagement(
            order_refresh_interval=int(os.getenv('BOT_ORDER_REFRESH_INTERVAL', '30')),
            max_orders_per_market=int(os.getenv('BOT_MAX_ORDERS_PER_MARKET', '2')),
            order_timeout_minutes=int(os.getenv('BOT_ORDER_TIMEOUT_MINUTES', '60')),
            batch_order_size=int(os.getenv('BOT_BATCH_ORDER_SIZE', '5')),
            order_retry_attempts=int(os.getenv('BOT_ORDER_RETRY_ATTEMPTS', '3')),
            order_retry_delay=int(os.getenv('BOT_ORDER_RETRY_DELAY', '5'))
        )
        
        return MarketMakingConfig(
            trading_mode=trading_mode,
            market_side=market_side,
            enabled=os.getenv('BOT_ENABLED', 'true').lower() == 'true',
            risk_limits=risk_limits,
            market_selection=market_selection,
            pricing_strategy=pricing_strategy,
            order_management=order_management,
            use_websockets=os.getenv('BOT_USE_WEBSOCKETS', 'true').lower() == 'true',
            log_level=os.getenv('BOT_LOG_LEVEL', 'INFO'),
            performance_tracking=os.getenv('BOT_PERFORMANCE_TRACKING', 'true').lower() == 'true',
            emergency_stop_enabled=os.getenv('BOT_EMERGENCY_STOP_ENABLED', 'true').lower() == 'true'
        )
    
    def save_config(self, config: MarketMakingConfig, file_path: Optional[str] = None):
        """Save configuration to file."""
        save_path = file_path or self.config_file
        
        data = config.to_dict()
        
        with open(save_path, 'w') as f:
            if save_path.endswith('.json'):
                json.dump(data, f, indent=2)
            elif save_path.endswith('.yaml') or save_path.endswith('.yml'):
                if yaml is None:
                    raise ImportError("PyYAML is required for YAML config files. Install with: pip install PyYAML")
                yaml.dump(data, f, default_flow_style=False)
            else:
                raise ValueError(f"Unsupported config file format: {save_path}")
        
        logger.info(f"Saved configuration to {save_path}")
    
    def get_preset_config(self, mode: TradingMode) -> MarketMakingConfig:
        """Get a preset configuration for a trading mode."""
        presets = {
            TradingMode.CONSERVATIVE: MarketMakingConfig(
                trading_mode=TradingMode.CONSERVATIVE,
                market_side=MarketSide.YES,
                risk_limits=RiskLimits(
                    max_position_per_market=5,
                    max_total_exposure=25,
                    max_order_size=2,
                    max_daily_loss=25.0,
                    stop_loss_percentage=0.03,
                    emergency_stop_loss=0.05,
                    max_markets=5
                ),
                market_selection=MarketSelection(
                    min_volume=2000,
                    max_spread_cents=3,
                    min_liquidity_dollars=200.0,
                    max_time_to_close_days=14,
                    min_time_to_close_days=2
                ),
                pricing_strategy=PricingStrategy(
                    default_spread_cents=3,
                    min_spread_cents=2,
                    max_spread_cents=8,
                    price_adjustment_factor=0.3
                ),
                order_management=OrderManagement(
                    order_refresh_interval=60,
                    max_orders_per_market=2,
                    order_timeout_minutes=120
                )
            ),
            
            TradingMode.MODERATE: MarketMakingConfig(
                trading_mode=TradingMode.MODERATE,
                market_side=MarketSide.YES,
                risk_limits=RiskLimits(
                    max_position_per_market=10,
                    max_total_exposure=50,
                    max_order_size=3,
                    max_daily_loss=50.0,
                    stop_loss_percentage=0.05,
                    emergency_stop_loss=0.10,
                    max_markets=8
                ),
                market_selection=MarketSelection(
                    min_volume=1000,
                    max_spread_cents=5,
                    min_liquidity_dollars=100.0,
                    max_time_to_close_days=21,
                    min_time_to_close_days=1
                ),
                pricing_strategy=PricingStrategy(
                    default_spread_cents=2,
                    min_spread_cents=1,
                    max_spread_cents=10,
                    price_adjustment_factor=0.5
                ),
                order_management=OrderManagement(
                    order_refresh_interval=30,
                    max_orders_per_market=2,
                    order_timeout_minutes=60
                )
            ),
            
            TradingMode.AGGRESSIVE: MarketMakingConfig(
                trading_mode=TradingMode.AGGRESSIVE,
                market_side=MarketSide.YES,
                risk_limits=RiskLimits(
                    max_position_per_market=20,
                    max_total_exposure=100,
                    max_order_size=5,
                    max_daily_loss=100.0,
                    stop_loss_percentage=0.08,
                    emergency_stop_loss=0.15,
                    max_markets=15
                ),
                market_selection=MarketSelection(
                    min_volume=500,
                    max_spread_cents=8,
                    min_liquidity_dollars=50.0,
                    max_time_to_close_days=30,
                    min_time_to_close_days=1
                ),
                pricing_strategy=PricingStrategy(
                    default_spread_cents=1,
                    min_spread_cents=1,
                    max_spread_cents=15,
                    price_adjustment_factor=0.8
                ),
                order_management=OrderManagement(
                    order_refresh_interval=15,
                    max_orders_per_market=2,
                    order_timeout_minutes=30
                )
            )
        }
        
        return presets.get(mode, presets[TradingMode.MODERATE])

# Example usage
if __name__ == "__main__":
    # Create configuration manager
    config_manager = BotConfigManager()
    
    # Load configuration
    config = config_manager.load_config()
    
    print("Current configuration:")
    print(json.dumps(config.to_dict(), indent=2, default=str))
    
    # Save a preset configuration
    conservative_config = config_manager.get_preset_config(TradingMode.CONSERVATIVE)
    config_manager.save_config(conservative_config, "conservative_config.yaml")
    
    print("\nSaved conservative configuration to conservative_config.yaml")
