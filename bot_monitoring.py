"""
Monitoring and Performance Tracking for Kalshi Market Making Bot.

This module provides comprehensive monitoring, logging, and performance tracking
for the market making bot, including real-time metrics, alerts, and reporting.
"""

import logging
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field, asdict
from collections import defaultdict, deque
import threading
import queue
import csv
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

@dataclass
class BotMetrics:
    """Bot performance metrics."""
    # Time tracking
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_update: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Order metrics
    total_orders_placed: int = 0
    total_orders_filled: int = 0
    total_orders_canceled: int = 0
    total_orders_rejected: int = 0
    
    # Trading metrics
    total_volume_traded: int = 0  # In contracts
    total_fees_paid: float = 0.0  # In dollars
    total_pnl: float = 0.0  # In dollars
    realized_pnl: float = 0.0  # In dollars
    unrealized_pnl: float = 0.0  # In dollars
    
    # Position metrics
    total_exposure: int = 0  # Total contracts across all positions
    active_markets: int = 0
    max_position_reached: int = 0
    
    # Performance metrics
    fill_rate: float = 0.0  # Percentage of orders that get filled
    average_fill_time: float = 0.0  # Average time from order to fill in seconds
    profit_per_trade: float = 0.0  # Average profit per filled order
    
    # Risk metrics
    max_drawdown: float = 0.0  # Maximum loss from peak
    current_drawdown: float = 0.0  # Current loss from peak
    sharpe_ratio: float = 0.0  # Risk-adjusted return metric
    
    # System metrics
    websocket_reconnects: int = 0
    api_errors: int = 0
    order_errors: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return asdict(self)
    
    def update_fill_rate(self):
        """Update fill rate calculation."""
        if self.total_orders_placed > 0:
            self.fill_rate = (self.total_orders_filled / self.total_orders_placed) * 100
    
    def update_profit_per_trade(self):
        """Update profit per trade calculation."""
        if self.total_orders_filled > 0:
            self.profit_per_trade = self.total_pnl / self.total_orders_filled

@dataclass
class Alert:
    """Alert/notification data structure."""
    timestamp: datetime
    level: str  # INFO, WARNING, ERROR, CRITICAL
    category: str  # RISK, PERFORMANCE, SYSTEM, ORDER
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert alert to dictionary."""
        result = asdict(self)
        result['timestamp'] = self.timestamp.isoformat()
        return result

class PerformanceTracker:
    """Track and analyze bot performance."""
    
    def __init__(self, max_history: int = 1000):
        """Initialize performance tracker."""
        self.max_history = max_history
        self.metrics = BotMetrics()
        self.historical_metrics: deque = deque(maxlen=max_history)
        self.trade_history: List[Dict[str, Any]] = []
        self.position_history: List[Dict[str, Any]] = []
        
        # Performance calculations
        self.peak_equity = 0.0
        self.equity_history: deque = deque(maxlen=100)  # Last 100 equity points
        
        # Thread safety
        self._lock = threading.Lock()
    
    def update_metrics(self, **kwargs):
        """Update bot metrics."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self.metrics, key):
                    setattr(self.metrics, key, value)
            
            self.metrics.last_update = datetime.now(timezone.utc)
            
            # Update derived metrics
            self.metrics.update_fill_rate()
            self.metrics.update_profit_per_trade()
            
            # Update equity tracking
            current_equity = self.metrics.total_pnl
            self.equity_history.append(current_equity)
            
            if current_equity > self.peak_equity:
                self.peak_equity = current_equity
            
            # Calculate drawdown
            self.metrics.current_drawdown = self.peak_equity - current_equity
            if len(self.equity_history) > 1:
                self.metrics.max_drawdown = max(
                    self.metrics.max_drawdown,
                    self.peak_equity - min(self.equity_history)
                )
    
    def record_trade(self, trade_data: Dict[str, Any]):
        """Record a trade for analysis."""
        with self._lock:
            trade_data['timestamp'] = datetime.now(timezone.utc)
            self.trade_history.append(trade_data)
            
            # Update metrics based on trade
            if 'volume' in trade_data:
                self.metrics.total_volume_traded += trade_data['volume']
            if 'fees' in trade_data:
                self.metrics.total_fees_paid += trade_data['fees']
            if 'pnl' in trade_data:
                self.metrics.realized_pnl += trade_data['pnl']
                self.metrics.total_pnl += trade_data['pnl']
    
    def record_position_update(self, position_data: Dict[str, Any]):
        """Record a position update."""
        with self._lock:
            position_data['timestamp'] = datetime.now(timezone.utc)
            self.position_history.append(position_data)
    
    def get_current_metrics(self) -> BotMetrics:
        """Get current metrics snapshot."""
        with self._lock:
            return BotMetrics(**self.metrics.to_dict())
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary."""
        with self._lock:
            runtime = datetime.now(timezone.utc) - self.metrics.start_time
            
            return {
                'runtime_hours': runtime.total_seconds() / 3600,
                'total_orders': self.metrics.total_orders_placed,
                'filled_orders': self.metrics.total_orders_filled,
                'fill_rate': self.metrics.fill_rate,
                'total_pnl': self.metrics.total_pnl,
                'realized_pnl': self.metrics.realized_pnl,
                'unrealized_pnl': self.metrics.unrealized_pnl,
                'total_fees': self.metrics.total_fees_paid,
                'net_pnl': self.metrics.total_pnl - self.metrics.total_fees_paid,
                'total_exposure': self.metrics.total_exposure,
                'active_markets': self.metrics.active_markets,
                'max_drawdown': self.metrics.max_drawdown,
                'current_drawdown': self.metrics.current_drawdown,
                'profit_per_trade': self.metrics.profit_per_trade,
                'api_errors': self.metrics.api_errors,
                'websocket_reconnects': self.metrics.websocket_reconnects
            }

class AlertManager:
    """Manage alerts and notifications."""
    
    def __init__(self):
        """Initialize alert manager."""
        self.alerts: deque = deque(maxlen=1000)  # Keep last 1000 alerts
        self.alert_callbacks: List[Callable[[Alert], None]] = []
        self.alert_filters: Dict[str, List[str]] = defaultdict(list)  # category -> levels
    
    def add_alert_callback(self, callback: Callable[[Alert], None]):
        """Add an alert callback function."""
        self.alert_callbacks.append(callback)
    
    def set_alert_filter(self, category: str, levels: List[str]):
        """Set alert filter for a category."""
        self.alert_filters[category] = levels
    
    def send_alert(self, level: str, category: str, message: str, data: Dict[str, Any] = None):
        """Send an alert."""
        alert = Alert(
            timestamp=datetime.now(timezone.utc),
            level=level,
            category=category,
            message=message,
            data=data or {}
        )
        
        # Add to alerts history
        self.alerts.append(alert)
        
        # Check if alert should be sent based on filters
        if self._should_send_alert(alert):
            # Log the alert
            log_level = getattr(logging, level.upper(), logging.INFO)
            logger.log(log_level, f"[{category}] {message}")
            
            # Call alert callbacks
            for callback in self.alert_callbacks:
                try:
                    callback(alert)
                except Exception as e:
                    logger.error(f"Error in alert callback: {e}")
    
    def _should_send_alert(self, alert: Alert) -> bool:
        """Check if alert should be sent based on filters."""
        if alert.category in self.alert_filters:
            return alert.level in self.alert_filters[alert.category]
        return True  # Send all alerts if no filter set
    
    def get_recent_alerts(self, limit: int = 50) -> List[Alert]:
        """Get recent alerts."""
        return list(self.alerts)[-limit:]
    
    def get_alerts_by_level(self, level: str, limit: int = 50) -> List[Alert]:
        """Get alerts by level."""
        return [alert for alert in self.alerts if alert.level == level][-limit:]

class BotMonitor:
    """Main bot monitoring system."""
    
    def __init__(self, log_file: Optional[str] = None):
        """Initialize bot monitor."""
        self.performance_tracker = PerformanceTracker()
        self.alert_manager = AlertManager()
        self.log_file = log_file or f"logs/bot_monitoring_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
        # Create logs directory
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        
        # Setup file logging
        self._setup_file_logging()
        
        # Monitoring state
        self.monitoring_active = False
        self.monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Add default alert callbacks
        self.alert_manager.add_alert_callback(self._log_alert)
        self.alert_manager.add_alert_callback(self._check_critical_alerts)
    
    def _setup_file_logging(self):
        """Setup file logging for monitoring."""
        file_handler = logging.FileHandler(self.log_file)
        file_handler.setLevel(logging.INFO)
        
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(formatter)
        
        # Add to root logger
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)
    
    def start_monitoring(self):
        """Start monitoring in background thread."""
        if self.monitoring_active:
            logger.warning("Monitoring already active")
            return
        
        self.monitoring_active = True
        self._stop_event.clear()
        
        self.monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            daemon=True
        )
        self.monitor_thread.start()
        
        logger.info("Bot monitoring started")
    
    def stop_monitoring(self):
        """Stop monitoring."""
        if not self.monitoring_active:
            return
        
        self.monitoring_active = False
        self._stop_event.set()
        
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        
        logger.info("Bot monitoring stopped")
    
    def _monitoring_loop(self):
        """Main monitoring loop."""
        while not self._stop_event.is_set():
            try:
                # Check performance metrics
                self._check_performance_metrics()
                
                # Check risk metrics
                self._check_risk_metrics()
                
                # Check system health
                self._check_system_health()
                
                # Wait before next check
                self._stop_event.wait(30)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                self._stop_event.wait(5)  # Brief pause on error
    
    def _check_performance_metrics(self):
        """Check performance metrics and send alerts."""
        metrics = self.performance_tracker.get_current_metrics()
        
        # Check fill rate
        if metrics.fill_rate < 10 and metrics.total_orders_placed > 10:
            self.alert_manager.send_alert(
                "WARNING", "PERFORMANCE",
                f"Low fill rate: {metrics.fill_rate:.1f}%",
                {"fill_rate": metrics.fill_rate, "total_orders": metrics.total_orders_placed}
            )
        
        # Check profit per trade
        if metrics.profit_per_trade < -1.0 and metrics.total_orders_filled > 5:
            self.alert_manager.send_alert(
                "WARNING", "PERFORMANCE",
                f"Negative profit per trade: ${metrics.profit_per_trade:.2f}",
                {"profit_per_trade": metrics.profit_per_trade}
            )
    
    def _check_risk_metrics(self):
        """Check risk metrics and send alerts."""
        metrics = self.performance_tracker.get_current_metrics()
        
        # Check drawdown
        if metrics.current_drawdown > 20.0:  # $20 drawdown
            self.alert_manager.send_alert(
                "WARNING", "RISK",
                f"High drawdown: ${metrics.current_drawdown:.2f}",
                {"current_drawdown": metrics.current_drawdown}
            )
        
        # Check total exposure
        if metrics.total_exposure > 80:  # 80% of max exposure
            self.alert_manager.send_alert(
                "WARNING", "RISK",
                f"High total exposure: {metrics.total_exposure}",
                {"total_exposure": metrics.total_exposure}
            )
        
        # Check daily loss
        if metrics.total_pnl < -25.0:  # $25 loss
            self.alert_manager.send_alert(
                "ERROR", "RISK",
                f"Daily loss limit approaching: ${metrics.total_pnl:.2f}",
                {"total_pnl": metrics.total_pnl}
            )
    
    def _check_system_health(self):
        """Check system health and send alerts."""
        metrics = self.performance_tracker.get_current_metrics()
        
        # Check API errors
        if metrics.api_errors > 10:
            self.alert_manager.send_alert(
                "ERROR", "SYSTEM",
                f"High number of API errors: {metrics.api_errors}",
                {"api_errors": metrics.api_errors}
            )
        
        # Check websocket reconnects
        if metrics.websocket_reconnects > 5:
            self.alert_manager.send_alert(
                "WARNING", "SYSTEM",
                f"Multiple websocket reconnects: {metrics.websocket_reconnects}",
                {"websocket_reconnects": metrics.websocket_reconnects}
            )
    
    def _log_alert(self, alert: Alert):
        """Log alert to file."""
        alert_data = alert.to_dict()
        with open(self.log_file, 'a') as f:
            f.write(json.dumps(alert_data) + '\n')
    
    def _check_critical_alerts(self, alert: Alert):
        """Check for critical alerts that require immediate attention."""
        if alert.level == "CRITICAL":
            # In a production system, this could send emails, SMS, etc.
            logger.critical(f"CRITICAL ALERT: {alert.message}")
    
    def record_order_placed(self, order_data: Dict[str, Any]):
        """Record that an order was placed."""
        self.performance_tracker.update_metrics(
            total_orders_placed=self.performance_tracker.metrics.total_orders_placed + 1
        )
    
    def record_order_filled(self, fill_data: Dict[str, Any]):
        """Record that an order was filled."""
        self.performance_tracker.update_metrics(
            total_orders_filled=self.performance_tracker.metrics.total_orders_filled + 1
        )
        
        # Record as trade
        self.performance_tracker.record_trade(fill_data)
    
    def record_order_canceled(self, order_data: Dict[str, Any]):
        """Record that an order was canceled."""
        self.performance_tracker.update_metrics(
            total_orders_canceled=self.performance_tracker.metrics.total_orders_canceled + 1
        )
    
    def record_order_rejected(self, order_data: Dict[str, Any]):
        """Record that an order was rejected."""
        self.performance_tracker.update_metrics(
            total_orders_rejected=self.performance_tracker.metrics.total_orders_rejected + 1
        )
        
        self.alert_manager.send_alert(
            "WARNING", "ORDER",
            f"Order rejected: {order_data.get('reason', 'Unknown reason')}",
            order_data
        )
    
    def record_position_update(self, position_data: Dict[str, Any]):
        """Record a position update."""
        self.performance_tracker.record_position_update(position_data)
        
        # Update exposure
        total_exposure = sum(abs(pos.get('position', 0)) for pos in [position_data])
        self.performance_tracker.update_metrics(total_exposure=total_exposure)
    
    def record_api_error(self, error_data: Dict[str, Any]):
        """Record an API error."""
        self.performance_tracker.update_metrics(
            api_errors=self.performance_tracker.metrics.api_errors + 1
        )
        
        self.alert_manager.send_alert(
            "ERROR", "SYSTEM",
            f"API error: {error_data.get('message', 'Unknown error')}",
            error_data
        )
    
    def record_websocket_reconnect(self):
        """Record a websocket reconnect."""
        self.performance_tracker.update_metrics(
            websocket_reconnects=self.performance_tracker.metrics.websocket_reconnects + 1
        )
        
        self.alert_manager.send_alert(
            "INFO", "SYSTEM",
            "Websocket reconnected",
            {"reconnect_count": self.performance_tracker.metrics.websocket_reconnects}
        )
    
    def get_status_report(self) -> Dict[str, Any]:
        """Get comprehensive status report."""
        return {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'monitoring_active': self.monitoring_active,
            'performance': self.performance_tracker.get_performance_summary(),
            'recent_alerts': [alert.to_dict() for alert in self.alert_manager.get_recent_alerts(10)],
            'critical_alerts': [alert.to_dict() for alert in self.alert_manager.get_alerts_by_level('CRITICAL', 5)]
        }
    
    def export_metrics(self, file_path: str):
        """Export metrics to CSV file."""
        metrics = self.performance_tracker.get_current_metrics()
        
        with open(file_path, 'w', newline='') as f:
            writer = csv.writer(f)
            
            # Write header
            writer.writerow(['Metric', 'Value', 'Timestamp'])
            
            # Write metrics
            for key, value in metrics.to_dict().items():
                writer.writerow([key, value, datetime.now(timezone.utc).isoformat()])
        
        logger.info(f"Metrics exported to {file_path}")

# Example usage
if __name__ == "__main__":
    # Create monitor
    monitor = BotMonitor()
    
    # Start monitoring
    monitor.start_monitoring()
    
    # Simulate some activity
    monitor.record_order_placed({"ticker": "TEST", "side": "yes", "price": 50})  # Price in cents
    monitor.record_order_filled({"ticker": "TEST", "side": "yes", "price": 50, "volume": 1, "fees": 2, "pnl": 5})  # Fees and PnL in cents
    
    # Get status report
    report = monitor.get_status_report()
    print(json.dumps(report, indent=2))
    
    # Stop monitoring
    monitor.stop_monitoring()
