#!/usr/bin/env python
"""
config.py
This module handles configuration settings including secret key generation
and debug mode settings.
"""

import os
import secrets

SECRET_KEY = secrets.token_hex(32)


def get_debug_mode():
    """
    Determine if the Flask app should run in debug mode based on environment variables.

    :return: True if debug mode should be enabled, False otherwise.
    """
    return os.getenv("FLASK_DEBUG", "False").lower() in ("true", "1", "t")
