#!/usr/bin/env python
"""
config.py
This module handles configuration settings including secret key generation
and debug mode settings.
"""

import os
import secrets

SECRET_KEY = secrets.token_hex(32)

# Set debug mode based on environment variable
def get_debug_mode():
    return os.getenv("FLASK_DEBUG", "False").lower() in (
        "true",
        "1",
        "t",
    )
