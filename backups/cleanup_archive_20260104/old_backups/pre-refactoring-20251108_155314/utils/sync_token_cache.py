#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Utility to sync token cache between kis_token.json and kis_token_cache.json
"""

import json
import os
from datetime import datetime
from pathlib import Path

def sync_token_cache():
    """Synchronize token cache files"""
    main_token_file = Path("data/kis_token.json")
    cache_token_file = Path("data/kis_token_cache.json")
    
    if not main_token_file.exists():
        print("ERROR: Main token file not found")
        return False
    
    try:
        # Read main token file
        with open(main_token_file, 'r', encoding='utf-8') as f:
            main_data = json.load(f)
        
        # Extract relevant data for cache
        cache_data = {
            "access_token": main_data["access_token"],
            "token_expired": main_data["expired_at"]
        }
        
        # Write to cache file
        with open(cache_token_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
        
        print("SUCCESS: Token cache synchronized")
        print(f"Token expires: {main_data['expired_at']}")
        return True
        
    except Exception as e:
        print(f"ERROR: Failed to sync token cache: {e}")
        return False

if __name__ == "__main__":
    sync_token_cache()