#!/usr/bin/env python
"""
config.py
This module handles configuration settings including secret key generation
and debug mode settings. It provides a centralized location for managing
application-wide configuration options.
"""

import os
import secrets

# Generate a secure secret key for the application
SECRET_KEY = secrets.token_hex(32)


def get_debug_mode():
    """Determine debug mode from environment variable.

    Returns:
        bool: True if debug mode is enabled, False otherwise.
        Debug mode is enabled if FLASK_DEBUG environment variable
        is set to 'true', '1', or 't' (case insensitive).
    """
    return os.getenv("FLASK_DEBUG", "False").lower() in (
        "true",
        "1",
        "t",
    )
