"""
Scheduler for running market screening every second.
"""
import time
import threading
import logging
from datetime import datetime, timedelta
from typing import List, Callable, Optional
import queue

from models import Market, ScreeningResult
from kalshi_client import KalshiAPIClient
from market_screener import MarketScreener
from config import Config

logger = logging.getLogger(__name__)

class MarketScheduler:
    """Scheduler for running market screening tasks."""
    
    def __init__(self, kalshi_client: KalshiAPIClient, screener: MarketScreener):
        """Initialize the scheduler."""
        self.kalshi_client = kalshi_client
        self.screener = screener
        self.is_running = False
        self.thread = None
        self.results_queue = queue.Queue()
        self.callbacks = []
        
        # Statistics
        self.total_runs = 0
        self.successful_runs = 0
        self.last_run_time = None
        self.last_results = []
    
    def add_callback(self, callback: Callable[[List[ScreeningResult]], None]):
        """Add a callback function to be called with screening results."""
        self.callbacks.append(callback)
    
    def start(self, interval_seconds: int = 1):
        """
        Start the scheduler.
        
        Args:
            interval_seconds: Interval between runs in seconds
        """
        if self.is_running:
            logger.warning("Scheduler is already running")
            return
        
        self.is_running = True
        self.thread = threading.Thread(
            target=self._run_loop,
            args=(interval_seconds,),
            daemon=True
        )
        self.thread.start()
        logger.info(f"Scheduler started with {interval_seconds}s interval")
    
    def stop(self):
        """Stop the scheduler."""
        if not self.is_running:
            logger.warning("Scheduler is not running")
            return
        
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Scheduler stopped")
    
    def _run_loop(self, interval_seconds: int):
        """Main scheduler loop."""
        logger.info("Scheduler loop started")
        
        while self.is_running:
            try:
                start_time = time.time()
                
                # Run screening
                self._run_screening()
                
                # Calculate sleep time
                elapsed = time.time() - start_time
                sleep_time = max(0, interval_seconds - elapsed)
                
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    logger.warning(f"Screening took {elapsed:.2f}s, longer than interval {interval_seconds}s")
                
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")
                time.sleep(interval_seconds)  # Wait before retrying
        
        logger.info("Scheduler loop ended")
    
    def _run_screening(self):
        """Run a single screening cycle."""
        try:
            self.total_runs += 1
            start_time = time.time()
            
            # Fetch events
            events = self.kalshi_client.get_events(limit=100, status="open", max_events=self.config.MAX_EVENTS)
            
            if not events:
                logger.warning("No events found in screening cycle")
                return
            
            # Screen events and their markets
            results = self.screener.screen_events(events)
            
            # Update statistics
            self.successful_runs += 1
            from datetime import timezone
            self.last_run_time = datetime.now(timezone.utc)
            self.last_results = results
            
            # Notify callbacks
            for callback in self.callbacks:
                try:
                    callback(results)
                except Exception as e:
                    logger.error(f"Error in callback: {e}")
            
            # Put results in queue for external access
            try:
                self.results_queue.put_nowait(results)
            except queue.Full:
                # Remove old results if queue is full
                try:
                    self.results_queue.get_nowait()
                    self.results_queue.put_nowait(results)
                except queue.Empty:
                    pass
            
            elapsed = time.time() - start_time
            total_markets = sum(len(event.markets) for event in events)
            logger.info(f"Screening cycle completed in {elapsed:.2f}s - {len(events)} events ({total_markets} markets), {len([r for r in results if r.is_profitable])} opportunities")
            
        except Exception as e:
            logger.error(f"Error in screening cycle: {e}")
    
    def get_latest_results(self) -> Optional[List[ScreeningResult]]:
        """Get the latest screening results."""
        try:
            return self.results_queue.get_nowait()
        except queue.Empty:
            return self.last_results
    
    def get_statistics(self) -> dict:
        """Get scheduler statistics."""
        success_rate = self.successful_runs / self.total_runs if self.total_runs > 0 else 0
        
        return {
            'is_running': self.is_running,
            'total_runs': self.total_runs,
            'successful_runs': self.successful_runs,
            'success_rate': success_rate,
            'last_run_time': self.last_run_time,
            'queue_size': self.results_queue.qsize()
        }

class MarketDataCollector:
    """Collects and stores market data over time."""
    
    def __init__(self, max_history: int = 1000):
        """Initialize the data collector."""
        self.max_history = max_history
        self.history = []
        self.lock = threading.Lock()
    
    def add_results(self, results: List[ScreeningResult]):
        """Add screening results to history."""
        with self.lock:
            from datetime import timezone
            timestamp = datetime.now(timezone.utc)
            self.history.append({
                'timestamp': timestamp,
                'results': results,
                'total_markets': len(results),
                'profitable_markets': len([r for r in results if r.is_profitable]),
                'avg_score': sum(r.score for r in results) / len(results) if results else 0
            })
            
            # Keep only recent history
            if len(self.history) > self.max_history:
                self.history = self.history[-self.max_history:]
    
    def get_history(self) -> List[dict]:
        """Get historical data."""
        with self.lock:
            return self.history.copy()
    
    def get_summary_stats(self, hours: int = 24) -> dict:
        """Get summary statistics for the last N hours."""
        with self.lock:
            from datetime import timezone
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            recent_data = [d for d in self.history if d['timestamp'] >= cutoff_time]
            
            if not recent_data:
                return {
                    'total_cycles': 0,
                    'avg_markets_per_cycle': 0,
                    'avg_opportunities_per_cycle': 0,
                    'avg_score': 0,
                    'best_score': 0
                }
            
            return {
                'total_cycles': len(recent_data),
                'avg_markets_per_cycle': sum(d['total_markets'] for d in recent_data) / len(recent_data),
                'avg_opportunities_per_cycle': sum(d['profitable_markets'] for d in recent_data) / len(recent_data),
                'avg_score': sum(d['avg_score'] for d in recent_data) / len(recent_data),
                'best_score': max(d['avg_score'] for d in recent_data)
            }
