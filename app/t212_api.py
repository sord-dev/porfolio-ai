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