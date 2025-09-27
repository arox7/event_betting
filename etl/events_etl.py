"""
Events ETL module for fetching and storing Kalshi events data.
"""

import logging
import time
from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta

from kalshi.http_client import KalshiHTTPClient
from kalshi.shared_utils import get_base_api_url
from database.models import TradesDatabase
from config import Config

logger = logging.getLogger(__name__)


class EventsETL:
    """ETL class for handling Kalshi events data."""
    
    def __init__(self):
        """Initialize EventsETL with Kalshi client and database."""
        self.config = Config()
        self.kalshi_client = KalshiHTTPClient(self.config)
        self.db = TradesDatabase()
    
    def should_run_etl(self) -> bool:
        """
        Check if ETL should run based on recent activity.
        Returns True if we haven't run recently or if there might be new events.
        """
        try:
            # Get the most recent event from our database
            events = self.db.get_all_events()
            if not events:
                logger.info("No events in database - ETL should run")
                return True
            
            # For now, always run ETL to ensure we get any new events
            # In the future, we could add logic to check if events were added recently
            logger.info(f"Database has {len(events)} events - ETL will run to check for updates")
            return True
            
        except Exception as e:
            logger.error(f"Error checking if ETL should run: {e}")
            return True  # Default to running if we can't determine
    
    def fetch_and_store_all_events(self, limit: int = None) -> Dict[str, Any]:
        """
        Fetch all available events and store them in the database using pagination.
        Optimized to only store new events and avoid duplicates.

        Args:
            limit: Maximum number of events to fetch (None = all)

        Returns:
            Dict with success status and results
        """
        try:
            logger.info(f"Fetching all events with pagination (limit: {limit if limit else 'unlimited'})")

            # Check if we should run ETL
            if not self.should_run_etl():
                logger.info("ETL check indicates no new events expected - skipping")
                return {
                    'success': True,
                    'events_fetched': 0,
                    'events_stored': 0,
                    'pages_fetched': 0,
                    'skipped': True
                }

            # Use direct HTTP API to bypass SDK validation issues
            import requests

            url = f"{get_base_api_url(self.kalshi_client)}/events"

            all_events = []
            cursor = None
            page_count = 0
            total_fetched = 0
            batch_size = 1000  # Process events in batches for efficiency

            while True:
                page_count += 1
                logger.info(f"Fetching page {page_count} (cursor: {cursor[:20] if cursor else 'None'}...)")

                # Build request parameters
                params = {'limit': 200}  # Use 200 as requested
                if cursor:
                    params['cursor'] = cursor

                # Retry logic for API calls
                max_retries = 3
                retry_delay = 2.0
                response = None

                for attempt in range(max_retries):
                    try:
                        response = requests.get(url, params=params, timeout=30)

                        if response.status_code == 200:
                            break
                        elif response.status_code in [502, 503, 504]:  # Server errors
                            if attempt < max_retries - 1:
                                logger.warning(f"API call failed with {response.status_code}, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                                time.sleep(retry_delay)
                                retry_delay *= 2  # Exponential backoff
                                continue
                            else:
                                return {
                                    'success': False,
                                    'error': f'API call failed after {max_retries} attempts: {response.status_code} - {response.text}',
                                    'events_fetched': total_fetched,
                                    'events_stored': 0
                                }
                        else:
                            return {
                                'success': False,
                                'error': f'API call failed: {response.status_code} - {response.text}',
                                'events_fetched': total_fetched,
                                'events_stored': 0
                            }
                    except requests.exceptions.RequestException as e:
                        if attempt < max_retries - 1:
                            logger.warning(f"Request failed: {e}, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                            time.sleep(retry_delay)
                            retry_delay *= 2
                            continue
                        else:
                            return {
                                'success': False,
                                'error': f'Request failed after {max_retries} attempts: {e}',
                                'events_fetched': total_fetched,
                                'events_stored': 0
                            }

                # Parse response
                data = response.json()
                events_data = data.get('events', [])
                cursor = data.get('cursor')

                if not events_data:
                    logger.info("No more events in response, pagination complete")
                    break

                all_events.extend(events_data)
                total_fetched += len(events_data)

                logger.info(f"Page {page_count}: {len(events_data)} events (total so far: {total_fetched})")

                # Check if we've reached the limit
                if limit and total_fetched >= limit:
                    logger.info(f"Reached limit of {limit} events")
                    all_events = all_events[:limit]
                    total_fetched = limit
                    break

                # Check if there's no more data
                if not cursor:
                    logger.info("No cursor returned, pagination complete")
                    break

                # Small delay between requests to be respectful
                time.sleep(0.1)

            if not all_events:
                return {
                    'success': False,
                    'error': 'No events returned from API',
                    'events_fetched': 0,
                    'events_stored': 0
                }

            # Process events in batches for efficiency
            stored_count = 0
            consecutive_empty_batches = 0
            max_empty_batches = 3  # Stop if we get 3 consecutive batches with no new events
            
            for i in range(0, len(all_events), batch_size):
                batch = all_events[i:i + batch_size]
                
                # Convert to our format
                batch_data = []
                for event_dict in batch:
                    event_data = {
                        'event_ticker': event_dict.get('event_ticker', ''),
                        'series_ticker': event_dict.get('series_ticker', ''),
                        'sub_title': event_dict.get('sub_title', ''),
                        'title': event_dict.get('title', ''),
                        'collateral_return_type': event_dict.get('collateral_return_type', ''),
                        'mutually_exclusive': event_dict.get('mutually_exclusive', False),
                        'category': event_dict.get('category', ''),
                        'price_level_structure': event_dict.get('price_level_structure', ''),
                        'available_on_brokers': event_dict.get('available_on_brokers', False)
                    }
                    batch_data.append(event_data)
                
                # Batch insert (automatically skips duplicates)
                batch_stored = self.db.insert_events_batch(batch_data)
                stored_count += batch_stored
                
                logger.info(f"Processed batch {i//batch_size + 1}: {len(batch_data)} events, {batch_stored} new")
                
                # Early exit optimization: if we're getting mostly duplicates, we can stop early
                if batch_stored == 0:
                    consecutive_empty_batches += 1
                    if consecutive_empty_batches >= max_empty_batches:
                        logger.info(f"Early exit: {consecutive_empty_batches} consecutive batches with no new events")
                        break
                else:
                    consecutive_empty_batches = 0  # Reset counter when we find new events

            logger.info(f"Successfully stored {stored_count} out of {total_fetched} events across {page_count} pages")

            return {
                'success': True,
                'events_fetched': total_fetched,
                'events_stored': stored_count,
                'pages_fetched': page_count
            }

        except Exception as e:
            logger.error(f"Error fetching all events: {e}")
            return {
                'success': False,
                'error': str(e),
                'events_fetched': 0,
                'events_stored': 0
            }
