"""
Trading212 API Client
Simple client for Trading212 API integration (NEW)
"""

import json
import base64
import requests
import os
import sys
import time
import threading
import random
import pickle
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any


class CachedTrading212API:
    """Enhanced Trading212 API client with caching and rate limiting"""
    
    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), 'trading212_config.json')
        
        self.config_path = config_path
        self.cache_file = os.path.join(os.path.dirname(__file__), '.trading212_cache.pkl')
        self.config = self.load_config()
        self.base_url = self.config.get('base_url', 'https://live.trading212.com/api/v0')
        self.headers = self._build_auth_headers()
        self.cache_duration = self.config.get('cache_duration', 300)  # 5 minutes
        
        # Rate limiting state
        self._rate_limit_lock = threading.Lock()
        self._rate_limit_info: Dict[str, Optional[int]] = {
            'limit': None,
            'remaining': None,
            'reset_timestamp': None,
            'period': None,
            'used': None
        }
        
        # Rate limiting configuration
        self._safety_margin = self.config.get('rate_limiting', {}).get('safety_margin', 0.1)  # 10% safety margin
        self._min_delay = self.config.get('rate_limiting', {}).get('min_delay', 1.0)  # Minimum 1 second delay
        self._backoff_factor = self.config.get('rate_limiting', {}).get('backoff_factor', 2.0)
        
    def load_config(self) -> Dict:
        """Load configuration from JSON file"""
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            
            if not config.get('api_key') or not config.get('api_secret'):
                raise ValueError("API key and secret must be configured")
            
            if 'PLACEHOLDER' in config.get('api_key', '') or 'PLACEHOLDER' in config.get('api_secret', ''):
                raise ValueError("Please replace placeholder credentials with actual Trading212 API keys")
            
            return config
        except FileNotFoundError:
            raise ValueError("Configuration file not found. Please create trading212_config.json")
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON in configuration file")
    
    def _build_auth_headers(self) -> Dict[str, str]:
        """Build authentication headers for Trading212 API"""
        credentials = f"{self.config['api_key']}:{self.config['api_secret']}"
        encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
        
        return {
            'Authorization': f'Basic {encoded_credentials}',
            'Content-Type': 'application/json'
        }
    
    def load_cache(self) -> Dict:
        """Load cached data if it exists and is still valid"""
        if not os.path.exists(self.cache_file):
            return {}
        
        try:
            with open(self.cache_file, 'rb') as f:
                cache_data = pickle.load(f)
            
            # Check if cache is still valid
            if 'timestamp' in cache_data:
                cache_age = datetime.now() - cache_data['timestamp']
                if cache_age.total_seconds() < self.cache_duration:
                    return cache_data
            
            return {}
        except (FileNotFoundError, pickle.PickleError, KeyError):
            return {}
    
    def save_cache(self, data: Dict):
        """Save data to cache with timestamp"""
        cache_data = {
            'timestamp': datetime.now(),
            'data': data
        }
        
        try:
            with open(self.cache_file, 'wb') as f:
                pickle.dump(cache_data, f)
        except Exception:
            pass  # Fail silently if can't write cache
    
    def _check_rate_limits(self):
        """Check if we should delay the request based on rate limits"""
        with self._rate_limit_lock:
            limit = self._rate_limit_info['limit']
            remaining = self._rate_limit_info['remaining']
            
            # Skip if we don't have rate limit info yet
            if limit is None or remaining is None:
                return
            
            # If we're within the safety margin, add delay
            if remaining <= (limit * self._safety_margin):
                # Calculate delay with exponential backoff and jitter
                # Ensure we don't get negative values
                requests_used = max(0, limit - remaining)
                delay = self._min_delay * (self._backoff_factor ** requests_used)
                jitter = random.uniform(0.5, 1.5)  # Add randomness
                delay *= jitter

                time.sleep(delay)
    
    def _update_rate_limit_info(self, response):
        """Update rate limit information from response headers"""
        with self._rate_limit_lock:
            headers = response.headers
            
            # Safely parse integer headers
            def safe_int(value):
                if value and value.strip():
                    try:
                        return int(value)
                    except (ValueError, TypeError):
                        return None
                return None
            
            # Directly assign values to avoid type checking issues
            self._rate_limit_info['limit'] = safe_int(headers.get('x-ratelimit-limit'))
            self._rate_limit_info['remaining'] = safe_int(headers.get('x-ratelimit-remaining'))
            self._rate_limit_info['reset_timestamp'] = safe_int(headers.get('x-ratelimit-reset'))
            self._rate_limit_info['period'] = safe_int(headers.get('x-ratelimit-period'))
            self._rate_limit_info['used'] = safe_int(headers.get('x-ratelimit-used'))
    
    def get_rate_limit_status(self) -> Dict:
        """Get current rate limit status"""
        with self._rate_limit_lock:
            status = self._rate_limit_info.copy()
            reset_timestamp = status.get('reset_timestamp')
            if reset_timestamp is not None:
                status['reset_in_seconds'] = max(0, reset_timestamp - int(time.time()))
            else:
                status['reset_in_seconds'] = None
            return status
    
    def make_request(self, endpoint: str, method: str = 'GET', data: Dict = None) -> Optional[Dict]:
        """Make authenticated request to Trading212 API with caching and rate limiting"""
        cache_key = f"{method}:{endpoint}"
        
        # Try to get from cache first
        cached_data = self.load_cache()
        if cache_key in cached_data.get('data', {}):
            return cached_data['data'][cache_key]
        
        # Check rate limits before making request
        self._check_rate_limits()
        
        url = f"{self.base_url}{endpoint}"
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=self.headers, timeout=10)
            elif method == 'POST':
                response = requests.post(url, headers=self.headers, json=data, timeout=10)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            # Update rate limit info from response headers
            self._update_rate_limit_info(response)
            
            response.raise_for_status()
            result = response.json()
            
            # Save successful result to cache
            if 'data' not in cached_data:
                cached_data = {'data': {}}
            cached_data['data'][cache_key] = result
            self.save_cache(cached_data['data'])
            
            return result
        
        except requests.exceptions.RequestException as e:
            # Return cached data if available during network errors
            cached_data = self.load_cache()
            if cache_key in cached_data.get('data', {}):
                return cached_data['data'][cache_key]
            return None
        except json.JSONDecodeError:
            return None
    
    def get_balance(self) -> Dict:
        """Get account summary information including cash and investments"""
        return self.make_request('/equity/account/summary')
    
    def get_positions(self) -> Dict:
        """Get all current positions"""
        return self.make_request('/equity/positions')
    
    def get_cash(self) -> Dict:
        """Get cash account information"""
        return self.make_request('/equity/account/cash')
    
    def get_portfolio(self) -> List[Dict]:
        """Get portfolio positions"""
        return self.make_request('/equity/portfolio')
    
    def get_account_info(self) -> Dict:
        """Get account information"""
        return self.make_request('/equity/account/info')
    
    def get_orders(self) -> List[Dict]:
        """Get pending orders"""
        return self.make_request('/equity/orders')
    
    def get_all_data(self) -> Dict:
        """Get all required data in one go and cache it"""
        cached_data = self.load_cache()
        if cached_data and 'data' in cached_data:
            # Return cached data if available
            all_data = {}
            endpoints = ['/equity/account/cash', '/equity/portfolio', '/equity/account/info', '/equity/orders']
            
            # Check if we have all endpoints cached
            cache_complete = all(f"GET:{endpoint}" in cached_data['data'] for endpoint in endpoints)
            
            if cache_complete:
                all_data['cash'] = cached_data['data']['GET:/equity/account/cash']
                all_data['portfolio'] = cached_data['data']['GET:/equity/portfolio']
                all_data['info'] = cached_data['data']['GET:/equity/account/info']
                all_data['orders'] = cached_data['data']['GET:/equity/orders']
                return all_data
        
        # Fetch fresh data with rate limit handling
        all_data = {}
        
        # Add small delays between requests to avoid rate limiting
        all_data['cash'] = self.make_request('/equity/account/cash')
        time.sleep(0.5)
        
        all_data['portfolio'] = self.make_request('/equity/portfolio')
        time.sleep(0.5)
        
        all_data['info'] = self.make_request('/equity/account/info')
        time.sleep(0.5)
        
        all_data['orders'] = self.make_request('/equity/orders')
        
        return all_data


class ConkyFormatter:
    """Format Trading212 data for Conky display"""
    
    @staticmethod
    def format_currency(amount: float, currency: str = "£", show_full: bool = False) -> str:
        """Format currency with appropriate symbol and comma separators"""
        if show_full:
            return f"{currency}{amount:,.2f}"
        elif abs(amount) >= 1000000:
            return f"{currency}{amount/1000000:.1f}M"
        elif abs(amount) >= 1000:
            return f"{currency}{amount/1000:.1f}K"
        else:
            return f"{currency}{amount:,.2f}"
    
    @staticmethod
    def format_percentage(pct: float) -> str:
        """Format percentage with color coding"""
        if pct > 0:
            return f"+{pct:.2f}%"
        else:
            return f"{pct:.2f}%"
    
    @staticmethod
    def get_color_code(value: float) -> str:
        """Get color code based on value (positive/negative)"""
        if value > 0:
            return "${color5}"  # green
        elif value < 0:
            return "${color6}"  # red
        else:
            return "${color}"


def main():
    """Main function to fetch and display Trading212 data"""
    try:
        # Initialize API client
        api = CachedTrading212API()
        
        # Fetch all data at once
        data = api.get_all_data()
        
        cash_data = data.get('cash')
        portfolio_data = data.get('portfolio')
        account_info = data.get('info')
        pending_orders = data.get('orders')
        
        if not cash_data:
            print("N/A")
            return
        
        # Calculate totals
        total_invested = cash_data.get('invested', 0)
        total_ppl = cash_data.get('ppl', 0)
        free_cash = cash_data.get('free', 0)
        total_value = cash_data.get('total', 0)
        
        # Calculate percentage return
        if total_invested > 0:
            pct_return = (total_ppl / total_invested) * 100
        else:
            pct_return = 0
        
        # Format output based on command line argument
        if len(sys.argv) > 1:
            data_type = sys.argv[1]
            
            if data_type == 'total_value':
                print(ConkyFormatter.format_currency(total_value, show_full=True))
            elif data_type == 'total_ppl':
                ppl_str = ConkyFormatter.format_currency(total_ppl, show_full=True)
                pct_str = ConkyFormatter.format_percentage(pct_return)
                print(f"{ppl_str} ({pct_str})")
            elif data_type == 'ppl_color':
                # Return just the color indicator for Conky to use
                print("positive" if total_ppl > 0 else "negative" if total_ppl < 0 else "neutral")
            elif data_type == 'total_ppl_colored':
                # Return P&L with embedded Conky color codes
                ppl_str = ConkyFormatter.format_currency(total_ppl, show_full=True)
                pct_str = ConkyFormatter.format_percentage(pct_return)
                if total_ppl > 0:
                    print(f"${{color5}}{ppl_str} ({pct_str})${{color}}")
                elif total_ppl < 0:
                    print(f"${{color6}}{ppl_str} ({pct_str})${{color}}")
                else:
                    print(f"{ppl_str} ({pct_str})")
            elif data_type == 'free_cash':
                print(ConkyFormatter.format_currency(free_cash, show_full=True))
            elif data_type == 'invested':
                print(ConkyFormatter.format_currency(total_invested, show_full=True))
            elif data_type == 'positions_count':
                print(len(portfolio_data) if portfolio_data else 0)
            elif data_type == 'pending_orders':
                print(len(pending_orders) if pending_orders else 0)
            elif data_type == 'top_position':
                if portfolio_data and len(portfolio_data) > 0:
                    def calculate_invested(pos):
                        """Calculate invested amount handling both pence and pound stocks"""
                        ticker = pos.get('ticker', '')
                        quantity = pos.get('quantity', 0)
                        avg_price = pos.get('averagePrice', 0)
                        
                        # Specific ETFs that are priced in pence despite being UK stocks
                        pence_etfs = ['CSP1', 'CSP2', 'VUKE', 'VHYL', 'VMID', 'VAPX', 'VUSA', 'VJPN', 'VFEU', 'VEUR', 'VWRL', 'VODL', 'IUKP', 'ISF', 'SUK2']
                        # US ETFs that might be priced in pence
                        us_pence_etfs = ['SPY', 'QQQ', 'IWM', 'DIA', 'VTI', 'VOO', 'IVV', 'GLD', 'SLV', 'TLT', 'HYG', 'LQD']
                        
                        clean_ticker = ticker.replace('_US_EQ', '').replace('_UK_EQ', '').replace('_L_EQ', '').replace('_l_EQ', '').replace('_EQ', '')
                        
                        # Determine if price is in pence
                        price_in_pence = False
                        
                        if clean_ticker in pence_etfs:
                            price_in_pence = True
                        elif clean_ticker in us_pence_etfs:
                            price_in_pence = True
                        elif ticker.lower().endswith(('l_eq', 'uk_eq')):
                            # For UK stocks, assume pence unless it's a known pound-priced ETF
                            if clean_ticker not in ['VWRP', 'VWRPl', 'VWRL', 'ISF', 'VMID']:  # Common UK ETFs priced in pounds
                                price_in_pence = True
                        
                        if price_in_pence:
                            # Price is in pence, convert to pounds
                            invested = quantity * (avg_price / 100)
                        else:
                            # Price is already in pounds
                            invested = quantity * avg_price
                        
                        return abs(invested)
                    
                    # Sort by calculated invested amount
                    top_pos = max(portfolio_data, key=calculate_invested)
                    ticker = top_pos.get('ticker', 'N/A').replace('_US_EQ', '').replace('_UK_EQ', '').replace('_L_EQ', '').replace('_l_EQ', '')
                    quantity = top_pos.get('quantity', 0)
                    avg_price = top_pos.get('averagePrice', 0)
                    ppl = top_pos.get('ppl', 0)
                    
                    # Calculate invested amount with proper currency handling
                    pence_etfs = ['CSP1', 'CSP2', 'VUKE', 'VHYL', 'VMID', 'VAPX', 'VUSA', 'VJPN', 'VFEU', 'VEUR', 'VWRL', 'VODL', 'IUKP', 'ISF', 'SUK2']
                    us_pence_etfs = ['SPY', 'QQQ', 'IWM', 'DIA', 'VTI', 'VOO', 'IVV', 'GLD', 'SLV', 'TLT', 'HYG', 'LQD']
                    clean_ticker = ticker.replace('_US_EQ', '').replace('_UK_EQ', '').replace('_L_EQ', '').replace('_l_EQ', '').replace('_EQ', '')
                    
                    # Determine if price is in pence
                    price_in_pence = False
                    
                    if clean_ticker in pence_etfs:
                        price_in_pence = True
                    elif clean_ticker in us_pence_etfs:
                        price_in_pence = True
                    elif ticker.lower().endswith(('l_eq', 'uk_eq')):
                        # For UK stocks, assume pence unless it's a known pound-priced ETF
                        if clean_ticker not in ['VWRP', 'VWRPl', 'VWRL', 'ISF', 'VMID']:  # Common UK ETFs priced in pounds
                            price_in_pence = True
                    
                    if price_in_pence:
                        invested = quantity * (avg_price / 100)
                    else:
                        invested = quantity * avg_price
                    
                    # Add direction indicator for short positions
                    direction = "SHORT " if quantity < 0 else ""
                    print(f"{ticker}: {direction}{ConkyFormatter.format_currency(abs(invested), show_full=True)}")
                else:
                    print("No positions")
            elif data_type == 'status':
                # API connection status
                print("Connected" if cash_data else "Disconnected")
            else:
                # Default: return summary
                print(f"£{total_value:.0f} | {ConkyFormatter.format_percentage(pct_return)}")
        
        else:
            # No argument provided, show summary
            print(f"Total: {ConkyFormatter.format_currency(total_value, show_full=True)}")
            print(f"P/L: {ConkyFormatter.format_currency(total_ppl, show_full=True)} ({ConkyFormatter.format_percentage(pct_return)})")
            print(f"Free: {ConkyFormatter.format_currency(free_cash, show_full=True)}")
    
    except ValueError as e:
        if "placeholder" in str(e).lower():
            print("Setup Required")
        else:
            print("Config Error")
    except Exception as e:
        print("N/A")


if __name__ == "__main__":
    main()