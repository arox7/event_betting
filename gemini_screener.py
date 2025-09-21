"""
Gemini-powered bespoke market screening using natural language queries.
"""
import logging
import re
import ast
import json
import os
from typing import List, Callable, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
import google.generativeai as genai

from models import Market, Event, ScreeningResult
from config import Config

logger = logging.getLogger(__name__)

# Set up file logging for Gemini interactions
def setup_gemini_logging():
    """Set up file logging for Gemini API interactions."""
    gemini_logger = logging.getLogger('gemini_interactions')
    gemini_logger.setLevel(logging.WARNING)  # Only log warnings and errors
    
    # Create logs directory if it doesn't exist
    os.makedirs('logs', exist_ok=True)
    
    # Create file handler
    log_file = f"logs/gemini_interactions_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    
    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    
    # Add handler to logger
    gemini_logger.addHandler(file_handler)
    
    return gemini_logger

# Initialize Gemini logger
gemini_logger = setup_gemini_logging()

class GeminiScreener:
    """Natural language to Python screening function converter and chat assistant using Gemini."""
    
    def __init__(self, config: Config):
        """Initialize the Gemini screener and chat assistant."""
        self.config = config
        
        # Configure Gemini
        if config.GEMINI_API_KEY:
            genai.configure(api_key=config.GEMINI_API_KEY)
            self.model = genai.GenerativeModel('gemini-2.5-flash')
            self.chat_session = None  # Will be initialized when chat starts
        else:
            self.model = None
            self.chat_session = None
            logger.warning("Gemini API key not configured. AI features will not be available.")
    
    def is_available(self) -> bool:
        """Check if Gemini screening is available."""
        return self.model is not None
    
    def explain_screening_criteria(self, user_prompt: str, screening_code: str) -> Optional[str]:
        """
        Explain what criteria the AI used for screening based on the user prompt and generated code.
        
        Args:
            user_prompt: Original user query
            screening_code: Generated Python screening function code
            
        Returns:
            Human-readable explanation of the criteria used, or None if generation fails
        """
        if not self.model:
            logger.error("Gemini model not available")
            return None
        
        try:
            prompt = f"""
You are an AI assistant that explains market screening criteria in simple terms.

User Query: "{user_prompt}"

Generated Screening Code:
```python
{screening_code}
```

Please analyze the screening code and explain in simple, human-readable terms what criteria were used to filter the markets. Be specific about:
1. Volume requirements (if any)
2. Spread requirements (if any) 
3. Time-based filters (if any)
4. Any other market characteristics that were filtered for

Include other relevant criteria as well if there are any.
Format your response as a clear, concise explanation that a trader would understand. Use bullet points if helpful.

Example format:
"The AI filtered for markets with:
• Volume greater than 5000 shares in the last 24 hours
• Spreads tighter than 5 cents (0.05)
• Closing within the next 2 hours"

Be specific about the exact values used in the code.
"""

            # Log the input (reduced verbosity)
            gemini_logger.debug(f"EXPLAIN_CRITERIA - INPUT: {user_prompt[:50]}...")

            response = self.model.generate_content(prompt)
            
            if response and response.text:
                explanation = response.text.strip()
                # Log the output (reduced verbosity)
                gemini_logger.debug(f"EXPLAIN_CRITERIA - OUTPUT: {len(explanation)} chars")
                return explanation
            else:
                gemini_logger.warning(f"EXPLAIN_CRITERIA - EMPTY RESPONSE:")
                gemini_logger.warning(f"Response object: {response}")
                logger.warning("Empty response from Gemini for criteria explanation")
                return None
                
        except Exception as e:
            gemini_logger.error(f"EXPLAIN_CRITERIA - ERROR:")
            gemini_logger.error(f"Error: {str(e)}")
            gemini_logger.error(f"Error type: {type(e)}")
            logger.error(f"Error generating criteria explanation: {e}")
            return None
    
    def generate_screening_function(self, user_prompt: str) -> Optional[str]:
        """
        Convert natural language prompt to Python screening function.
        
        Args:
            user_prompt: Natural language description of screening criteria
            
        Returns:
            Python function code as string, or None if generation fails
        """
        if not self.model:
            logger.error("Gemini model not available")
            return None
        
        # Create the system prompt with market schema and examples
        system_prompt = self._build_system_prompt()
        
        # Build the full prompt
        full_prompt = f"""{system_prompt}

USER REQUEST: {user_prompt}

Please generate a Python screening function based on the above request. The function should be named `screen_markets` and take a list of Market objects as input."""
        
        try:
            # Log the input (reduced verbosity)
            gemini_logger.debug(f"GENERATE_SCREENING - INPUT: {user_prompt[:50]}...")
            
            # Validate user prompt
            if not user_prompt or not user_prompt.strip():
                gemini_logger.error("User prompt is empty or None")
                logger.error("User prompt is empty or None")
                return None
            
            response = self.model.generate_content(full_prompt)
            
            # Log the raw response (reduced verbosity)
            gemini_logger.debug(f"GENERATE_SCREENING - RAW RESPONSE: {len(response.text)} chars")
            
            if response.text:
                # Extract Python code from response
                code = self._extract_python_code(response.text)
                if code:
                    # Validate the generated code
                    if self._validate_screening_function(code):
                        gemini_logger.debug(f"GENERATE_SCREENING - SUCCESS: {len(code)} chars")
                        return code
                    else:
                        gemini_logger.warning(f"GENERATE_SCREENING - VALIDATION FAILED:")
                        gemini_logger.warning(f"Code that failed: {code}")
                        logger.error("Generated code failed validation")
                        return None
                else:
                    gemini_logger.warning(f"GENERATE_SCREENING - NO CODE EXTRACTED:")
                    gemini_logger.warning(f"Response text: {response.text}")
                    logger.error("No valid Python code found in Gemini response")
                    return None
            else:
                gemini_logger.error(f"GENERATE_SCREENING - EMPTY RESPONSE:")
                gemini_logger.error(f"Response object: {response}")
                logger.error("Empty response from Gemini")
                return None
                
        except Exception as e:
            gemini_logger.error(f"GENERATE_SCREENING - ERROR:")
            gemini_logger.error(f"Error: {str(e)}")
            gemini_logger.error(f"Error type: {type(e)}")
            logger.error(f"Error generating screening function: {e}")
            return None
    
    def execute_screening_function(self, code: str, markets: List[Market], events: List[Event]) -> List[ScreeningResult]:
        """
        Safely execute the generated screening function on markets with their events.
        
        Args:
            code: Python function code
            markets: List of Market objects
            events: List of Event objects (markets must belong to these events)
            
        Returns:
            List of screening results
        """
        if not markets or not events:
            return []
        
        # Create market-to-event lookup for efficiency
        market_to_event = {}
        for event in events:
            for market in event.markets:
                market_to_event[market.ticker] = event
        
        return self._execute_screening_direct(code, markets, market_to_event)
    
    def _execute_screening_direct(self, code: str, markets: List[Market], market_to_event: Dict[str, Event]) -> List[ScreeningResult]:
        """Execute screening function directly on markets without unnecessary conversions."""
        try:
            # Create safe execution environment
            safe_globals = self._create_safe_execution_environment()
            safe_locals = {}
            
            # Execute the code
            exec(code, safe_globals, safe_locals)
            
            # Get the screening function
            if 'screen_markets' not in safe_locals:
                logger.error("Generated code does not contain 'screen_markets' function")
                return []
            
            screen_function = safe_locals['screen_markets']
            
            # Execute screening directly on markets
            results = []
            for market in markets:
                # Get the event for this market
                event = market_to_event.get(market.ticker)
                if not event:
                    continue  # Skip markets without events
                
                try:
                    # Call the screening function directly
                    passes, reasons = screen_function(market, event)
                    
                    # Create screening result
                    result = ScreeningResult(
                        market=market,
                        event=event,
                        score=1.0 if passes else 0.0,
                        reasons=reasons if isinstance(reasons, list) else [str(reasons)]
                    )
                    results.append(result)
                    
                except Exception as e:
                    logger.warning(f"Error screening market {market.ticker}: {e}")
                    # Create failed result
                    failed_result = ScreeningResult(
                        market=market,
                        event=event,
                        score=0.0,
                        reasons=[f"Screening error: {str(e)}"]
                    )
                    results.append(failed_result)
            
            return results
            
        except Exception as e:
            logger.error(f"Error executing screening function: {e}")
            return []
    
    def _create_screening_results_from_markets(self, markets: List[Market], events: List[Event]) -> List[ScreeningResult]:
        """Create ScreeningResult objects from markets and events."""
        results = []
        for market in markets:
            # Find corresponding event
            event = None
            for e in events:
                if any(m.ticker == market.ticker for m in e.markets):
                    event = e
                    break
            
            # Create a basic screening result (will be re-screened)
            result = ScreeningResult(
                market=market,
                event=event,
                score=0.0,
                reasons=[]
            )
            results.append(result)
        
        return results
    
    def _execute_screening_on_results(self, code: str, screening_results: List[ScreeningResult]) -> List[ScreeningResult]:
        """Execute screening function on ScreeningResult objects."""
        try:
            # Create safe execution environment
            safe_globals = self._create_safe_execution_environment()
            safe_locals = {}
            
            
            # Execute the code
            exec(code, safe_globals, safe_locals)
            
            # Get the screening function
            if 'screen_markets' not in safe_locals:
                logger.error("Generated code does not contain 'screen_markets' function")
                return []
            
            screen_function = safe_locals['screen_markets']
            
            # Execute screening using existing market-event pairs
            results = []
            for result in screening_results:
                market = result.market
                event = result.event
                
                try:
                    # Call the screening function with the existing market-event pair
                    passes, reasons = screen_function(market, event)
                    
                    # Create new screening result
                    new_result = ScreeningResult(
                        market=market,
                        event=event,
                        score=1.0 if passes else 0.0,
                        reasons=reasons if isinstance(reasons, list) else [str(reasons)]
                    )
                    results.append(new_result)
                    
                except Exception as e:
                    error_msg = str(e)
                    if "__import__ not found" in error_msg:
                        error_msg = "Code tried to import modules - imports not allowed in screening functions"
                    logger.error(f"Error screening market {getattr(market, 'ticker', 'UNKNOWN')}: {error_msg}")
                    # Add failed result
                    failed_result = ScreeningResult(
                        market=market,
                        event=event,
                        score=0.0,
                        reasons=[f"Screening error: {error_msg}"]
                    )
                    results.append(failed_result)
            
            return results
            
        except Exception as e:
            logger.error(f"Error executing screening function: {e}")
            return []
    
    def execute_screening_function_from_results(self, code: str, screening_results: List[ScreeningResult]) -> List[ScreeningResult]:
        """
        Safely execute the generated screening function using existing screening results.
        
        This method is kept for backward compatibility but now delegates to execute_screening_function.
        
        Args:
            code: Python function code
            screening_results: List of existing screening results with market-event pairs
            
        Returns:
            List of new screening results
        """
        return self.execute_screening_function(code, screening_results)
    
    def _create_safe_execution_environment(self) -> dict:
        """Create a safe execution environment for generated code."""
        # Import datetime components that might be needed
        from datetime import datetime, timezone, timedelta
        import math
        import re
        
        # Create safe builtins - start with full builtins then disable dangerous ones
        safe_builtins = __builtins__.copy() if isinstance(__builtins__, dict) else __builtins__.__dict__.copy()
        
        # Create a safe __import__ function that allows safe imports but blocks dangerous ones
        def safe_import(name, *args, **kwargs):
            # Allow safe imports that are commonly needed by Python operations
            safe_imports = {
                # Datetime and time related
                'locale', '_locale', 'time', '_strptime', 'calendar', 
                'datetime', '_datetime', 'zoneinfo', 'pytz',
                
                # Math and numerical operations
                'math', 'cmath', 'decimal', 'fractions', 'random',
                'statistics', 'numbers',
                
                # String and text processing
                're', 'string', 'unicodedata', 'codecs',
                
                # Data structures and algorithms
                'operator', 'collections', 'functools', 'itertools',
                'heapq', 'bisect', 'array',
                
                # JSON and data formats (for data analysis)
                'json', 'csv', 'base64', 'binascii',
                
                # System info (safe parts)
                'sys', 'platform', 'struct', 'copy', 'weakref',
                
                # Commonly used by pandas/numpy internally
                'warnings', 'inspect', 'types', 'enum', 'abc',
                'contextlib', 'threading', '_thread',
                
                # Encoding/decoding
                'encodings', '_codecs', 'io', '_io'
            }
            
            if name in safe_imports:
                # Allow the import using the real __import__
                return __import__(name, *args, **kwargs)
            else:
                logger.error(f"BLOCKED IMPORT ATTEMPT: {name} (args={args}, kwargs={kwargs})")
                raise ImportError(f"Import not allowed in screening functions: {name}")
        
        
        # Disable dangerous functions
        safe_builtins['__import__'] = safe_import
        safe_builtins['exec'] = None
        safe_builtins['eval'] = None
        safe_builtins['open'] = None
        safe_builtins['compile'] = None
        if 'input' in safe_builtins:
            safe_builtins['input'] = None
        if 'raw_input' in safe_builtins:
            safe_builtins['raw_input'] = None
        
        # Try to import optional libraries
        safe_env = {
            '__builtins__': safe_builtins,
            # Make datetime components directly available
            'datetime': datetime,
            'timezone': timezone,
            'timedelta': timedelta,
            'Market': Market,
            'Event': Event,
            'ScreeningResult': ScreeningResult,
            # Math and regex
            'math': math,
            're': re,
        }
        
        # Add numpy if available
        try:
            import numpy as np
            safe_env['np'] = np
            safe_env['numpy'] = np
        except ImportError:
            logger.debug("NumPy not available in screening environment")
        
        # Add statistics if available
        try:
            import statistics
            safe_env['statistics'] = statistics
        except ImportError:
            logger.debug("Statistics not available in screening environment")
        
        return safe_env
    
    def _build_system_prompt(self) -> str:
        """Build the system prompt for Gemini with market schema and examples."""
        return """You are an expert Python programmer specializing in financial market analysis. Your task is to convert natural language requests into Python screening functions for Kalshi prediction markets.

MARKET SCHEMA (from Kalshi API):
```python
class Market:
    ticker: Optional[str]                    # Market identifier (e.g., "PRES24DEM")
    series_ticker: Optional[str]             # Series identifier
    event_ticker: Optional[str]              # Event identifier
    title: Optional[str]                     # Market title/question
    subtitle: Optional[str]                  # Additional description
    open_time: Optional[datetime]            # When market opened
    close_time: Optional[datetime]           # When market closes
    expiration_time: Optional[datetime]      # When market expires/settles (use close_time instead)
    status: Optional[str]                    # 'initialized', 'active', 'closed', 'settled', 'determined'
    yes_bid: Optional[float]                 # Current Yes bid price (cents)
    yes_ask: Optional[float]                 # Current Yes ask price (cents)
    no_bid: Optional[float]                  # Current No bid price (cents)
    no_ask: Optional[float]                  # Current No ask price (cents)
    last_price: Optional[float]              # Last traded price (cents)
    volume: Optional[int]                    # Total volume (in # of contracts)
    volume_24h: Optional[int]                # 24h volume (in # of contracts)
    result: Optional[str]                    # Settlement result: 'yes', 'no', or ''
    can_close_early: Optional[bool]          # Can market close early
    cap_count: Optional[int]                 # Count data
    
    # Computed properties (from our extended model):
    mid_price: float                         # (yes_bid + yes_ask) / 2 in dollars
    spread_percentage: Optional[float]       # Spread as percentage
    spread_cents: Optional[int]              # Spread in cents
    days_to_close: int                       # Days until market closes
```

class Event:
    event_ticker: Optional[str]              # Event identifier
    series_ticker: Optional[str]             # Series identifier
    title: Optional[str]                     # Event title
    sub_title: Optional[str]                 # Event subtitle
    status: Optional[str]                    # Event status
    markets: List[Market]                    # Markets in this event
    category: Optional[str]                  # Event category (from our model)

PRE-IMPORTED MODULES:
The following modules are already imported and available in the execution environment:
- datetime (datetime, timezone, timedelta)
- math
- re
- All standard Python built-ins

DO NOT include any import statements in your code. All required modules are pre-imported.

REQUIRED FUNCTION FORMAT:
You must generate a function with this exact signature:

```python
def screen_markets(market: Market, event: Event) -> tuple[bool, list[str]]:
    \"\"\"
    Screen a single market based on criteria.
    
    Args:
        market: Market object to screen
        event: Event object (may be None)
        
    Returns:
        tuple: (passes_screening: bool, reasons: list[str])
    \"\"\"
    # Your screening logic here
    passes = True
    reasons = []
    
    # Example logic:
    if market.volume and market.volume < 1000:
        passes = False
        reasons.append("Volume too low")
    
    return passes, reasons
```

EXAMPLES:

User: "find markets closing in the next 5 minutes"
Response:
```python
def screen_markets(market: Market, event: Event) -> tuple[bool, list[str]]:
    \"\"\"Find markets closing in the next 5 minutes.\"\"\"
    passes = False
    reasons = []
    
    if market.close_time:
        now = datetime.now(timezone.utc)
        time_to_close = market.close_time - now
        
        if time_to_close <= timedelta(minutes=5) and time_to_close > timedelta(0):
            passes = True
            minutes_left = int(time_to_close.total_seconds() / 60)
            reasons.append(f"Closes in {minutes_left} minutes")
        else:
            reasons.append(f"Closes in {time_to_close}")
    else:
        reasons.append("No close time available")
    
    return passes, reasons
```

**DATETIME SAFETY EXAMPLE** (how to safely handle datetime operations):
```python
def screen_markets(market: Market, event: Event) -> tuple[bool, list[str]]:
    \"\"\"Find markets that opened today - SAFE datetime handling.\"\"\"
    passes = False
    reasons = []
    
    # SAFE: Check if open_time exists before calling methods on it
    if market.open_time:
        now_utc = datetime.now(timezone.utc)
        today_utc_date = now_utc.date()
        
        # SAFE: Convert timezone, then get date
        market_open_date_utc = market.open_time.astimezone(timezone.utc).date()
        
        if market_open_date_utc == today_utc_date:
            passes = True
            reasons.append(f"Opened today: {market.open_time.strftime('%Y-%m-%d %H:%M UTC')}")
        else:
            reasons.append(f"Did not open today (opened {market_open_date_utc})")
    else:
        # SAFE: Handle None case
        reasons.append("No open time available")
    
    return passes, reasons
```

User: "show me markets with high volume but tight spreads"
Response:
```python
def screen_markets(market: Market, event: Event) -> tuple[bool, list[str]]:
    \"\"\"Find markets with high volume but tight spreads.\"\"\"
    passes = False
    reasons = []
    
    # Check volume
    high_volume = market.volume and market.volume >= 5000
    
    # Check spread
    tight_spread = market.spread_cents and market.spread_cents <= 5
    
    if high_volume and tight_spread:
        passes = True
        reasons.append(f"High volume: {market.volume:,}")
        reasons.append(f"Tight spread: {market.spread_cents}¢")
    else:
        if not high_volume:
            reasons.append(f"Volume too low: {market.volume or 0:,}")
        if not tight_spread:
            reasons.append(f"Spread too wide: {market.spread_cents or 0}¢")
    
    return passes, reasons
```

User: "find markets with prices that deviate significantly from 50% using statistical analysis"
Response:
```python
def screen_markets(market: Market, event: Event) -> tuple[bool, list[str]]:
    \"\"\"Find markets with prices that deviate significantly from 50% using statistical analysis.\"\"\"
    passes = False
    reasons = []
    
    if market.mid_price is not None:
        # Calculate deviation from 50%
        deviation = abs(market.mid_price - 0.5)
        
        # Use statistical threshold (2 standard deviations)
        # Assuming normal distribution around 50%
        threshold = 0.2  # Approximately 2 standard deviations for binary markets
        
        if deviation >= threshold:
            passes = True
            percentage = market.mid_price * 100
            z_score = deviation / 0.1  # Rough z-score calculation
            reasons.append(f"Price {percentage:.1f}% deviates significantly from 50%")
            reasons.append(f"Z-score: {z_score:.2f}")
        else:
            reasons.append(f"Price {market.mid_price * 100:.1f}% is close to 50%")
    else:
        reasons.append("No price data available")
    
    return passes, reasons
```

AVAILABLE MODULES (already imported, no import statements needed):
- datetime, timezone, timedelta: For time operations
- math: Mathematical functions
- re: Regular expressions
- np, numpy: NumPy for numerical operations (if installed)
- pd, pandas: Pandas for data analysis (if installed)  
- statistics: Statistical functions (if installed)
- Market, Event, ScreeningResult: Data models

IMPORTANT RULES:
1. Always return a tuple: (bool, list[str])
2. Handle None values safely using 'if' conditions before accessing methods/attributes
3. Use timezone-aware datetime operations
4. Provide descriptive reasons for both passing and failing
5. Function must be named 'screen_markets'
6. Include docstring explaining the screening logic
7. Handle edge cases gracefully
8. DO NOT use any import statements - all modules listed above are pre-imported
9. DO NOT use __import__, exec, eval, or other dangerous functions
10. You can use numpy, pandas, math, statistics, and regex for advanced analysis

CRITICAL SAFETY PATTERNS:
- ALWAYS check if datetime fields exist: `if market.open_time:` before calling `.astimezone()` or `.strftime()`
- ALWAYS check if numeric fields exist: `if market.volume is not None:` before comparisons
- ALWAYS use safe property access: `getattr(market, 'field', default_value)` for optional fields
- NEVER call methods on potentially None values
- Use .strftime() for date formatting: `market.open_time.strftime('%Y-%m-%d %H:%M UTC')`
"""
    
    def _extract_python_code(self, response_text: str) -> Optional[str]:
        """Extract Python code from Gemini response."""
        # Look for code blocks
        code_block_pattern = r'```python\n(.*?)```'
        matches = re.findall(code_block_pattern, response_text, re.DOTALL)
        
        if matches:
            return matches[0].strip()
        
        # If no code blocks, try to find function definition
        lines = response_text.split('\n')
        code_lines = []
        in_function = False
        
        for line in lines:
            if line.strip().startswith('def screen_markets'):
                in_function = True
            
            if in_function:
                code_lines.append(line)
                
                # Simple heuristic: if we hit a line that's not indented and not empty, we're done
                if line.strip() and not line.startswith(' ') and not line.startswith('\t') and not line.strip().startswith('def'):
                    if len(code_lines) > 1:  # We have more than just the def line
                        break
        
        if code_lines:
            return '\n'.join(code_lines).strip()
        
        return None
    
    def _validate_screening_function(self, code: str) -> bool:
        """Validate that the generated code is safe and correct."""
        try:
            # Parse the code to check syntax
            tree = ast.parse(code)
            
            # Check that it contains a function named 'screen_markets'
            has_screen_function = False
            
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == 'screen_markets':
                    has_screen_function = True
                    # Check function signature
                    if len(node.args.args) != 2:
                        logger.error("screen_markets function must have exactly 2 arguments")
                        return False
                
                # Check for dangerous operations - NO imports allowed since we provide everything
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    logger.error("Import statements are not allowed - all required modules are pre-imported")
                    return False
                
                # Check for dangerous function calls
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        dangerous_funcs = ['exec', 'eval', 'open', '__import__', 'getattr', 'setattr', 'delattr']
                        if node.func.id in dangerous_funcs:
                            logger.error(f"Unsafe function call: {node.func.id}")
                            return False
            
            if not has_screen_function:
                logger.error("Code does not contain 'screen_markets' function")
                return False
            
            return True
            
        except SyntaxError as e:
            logger.error(f"Syntax error in generated code: {e}")
            return False
        except Exception as e:
            logger.error(f"Error validating code: {e}")
            return False
    
    def start_chat_session(self, markets: List[Market] = None, events: List[Event] = None) -> None:
        """
        Start a new chat session with context about current markets.
        
        Args:
            markets: Current list of markets for context
            events: Current list of events for context
        """
        if not self.model:
            logger.error("Gemini model not available")
            return
        
        # Build context-aware system prompt
        system_prompt = self._build_chat_system_prompt(markets, events)
        
        # Start chat session
        self.chat_session = self.model.start_chat(history=[])
        
        # Send system context as first message
        try:
            self.chat_session.send_message(system_prompt)
        except Exception as e:
            logger.error(f"Error starting chat session: {e}")
            self.chat_session = None
    
    def chat(self, user_message: str) -> Optional[str]:
        """
        Send a message to the chat session and get a response.
        
        Args:
            user_message: User's message/question
            
        Returns:
            AI response or None if error
        """
        if not self.chat_session:
            logger.error("Chat session not started")
            return None
        
        try:
            response = self.chat_session.send_message(user_message)
            return response.text if response.text else None
        except Exception as e:
            logger.error(f"Error in chat: {e}")
            return f"Sorry, I encountered an error: {str(e)}"
    
    def reset_chat(self):
        """Reset the chat session."""
        self.chat_session = None
    
    def _build_chat_system_prompt(self, markets: List[Market] = None, events: List[Event] = None) -> str:
        """Build system prompt for chat mode with market context."""
        
        # Basic market statistics for context
        market_context = ""
        if markets:
            total_markets = len(markets)
            active_markets = len([m for m in markets if m.status == 'active'])
            total_volume = sum(m.volume or 0 for m in markets)
            avg_price = sum(m.mid_price or 0 for m in markets if m.mid_price) / len([m for m in markets if m.mid_price]) if markets else 0
            
            market_context = f"""
CURRENT MARKET CONTEXT:
- Total markets available: {total_markets:,}
- Active markets: {active_markets:,}
- Total volume across all markets: {total_volume:,}
- Average mid price: ${avg_price:.2f}
"""
        
        # Event context
        event_context = ""
        if events:
            event_categories = {}
            for event in events:
                if event.category:
                    event_categories[event.category] = event_categories.get(event.category, 0) + 1
            
            if event_categories:
                event_context = f"""
EVENT CATEGORIES:
{chr(10).join(f'- {cat}: {count} events' for cat, count in event_categories.items())}
"""
        
        return f"""You are an expert AI assistant specializing in Kalshi prediction markets and trading. You help users understand markets, analyze trading opportunities, and answer questions about prediction market trading.

KALSHI PLATFORM OVERVIEW:
Kalshi is a regulated prediction market platform where users can trade on real-world events. Markets are binary (Yes/No) contracts that settle based on event outcomes.

MARKET SCHEMA (for reference):
- ticker: Market identifier (e.g., "PRES24DEM")
- title: Market question/description
- status: Market status (active, closed, settled, etc.)
- yes_bid/yes_ask: Current Yes side pricing (in cents)
- no_bid/no_ask: Current No side pricing (in cents)
- last_price: Most recent trade price
- volume: Total trading volume
- volume_24h: 24-hour trading volume
- mid_price: Midpoint between bid/ask
- spread: Difference between bid/ask
- close_time: When market stops trading
- expiration_time: When market settles (deprecated, use close_time)

{market_context}

{event_context}

CAPABILITIES:
1. Answer questions about prediction markets and trading concepts
2. Explain Kalshi platform features and mechanics
3. Analyze market data and trends
4. Provide trading insights and strategies
5. Help interpret market prices and probabilities
6. Generate screening functions when requested (say "I can help you create a screening function for that")

IMPORTANT GUIDELINES:
- Always provide helpful, accurate information about prediction markets
- When discussing specific markets, reference the current data when available
- Explain concepts clearly for both beginners and experienced traders
- Never provide financial advice - focus on education and analysis
- If asked to screen markets, offer to generate a screening function
- Stay focused on Kalshi, prediction markets, and trading topics

RESPONSE STYLE:
- Be conversational and helpful
- Use emojis sparingly but effectively
- Provide concrete examples when explaining concepts
- Break down complex topics into digestible parts
- Ask clarifying questions when user intent is unclear

How can I help you with Kalshi markets and trading today?"""
