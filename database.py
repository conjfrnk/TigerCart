#!/usr/bin/env python
"""
database.py
Populates tigercart.sqlite3 and users.sqlite3
"""

import sqlite3

# Define database file names
MAIN_DATABASE = "tigercart.sqlite3"
USER_DATABASE = "users.sqlite3"


def get_main_db_connection():
    """Establishes and returns a connection to the main database."""
    conn = sqlite3.connect(MAIN_DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def get_user_db_connection():
    """Establishes and returns a connection to the user database."""
    conn = sqlite3.connect(USER_DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_main_db():
    """Initializes the main database with necessary tables."""
    conn = get_main_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            category TEXT NOT NULL
        )
    """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            status TEXT CHECK(status IN ('placed', 'claimed', 'fulfilled', 'cancelled')),
            time_placed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER,
            items TEXT,
            prices TEXT,
            quantities TEXT,
            delivery_location TEXT
        )
    """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cart (
            item_id TEXT,
            quantity INTEGER,
            user_id INTEGER,
            PRIMARY KEY (item_id, user_id)
        )
    """
    )

    conn.commit()
    conn.close()


def init_user_db():
    """Initializes the user database with necessary tables."""
    conn = get_user_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            cart TEXT DEFAULT '{}'
        )
    """
    )

    conn.commit()
    conn.close()


def populate_items(sample_items):
    """Populates the items table with sample data."""
    conn = get_main_db_connection()
    cursor = conn.cursor()

    for item_id, item in sample_items.items():
        cursor.execute(
            "INSERT OR IGNORE INTO items (id, name, price, category) VALUES (?, ?, ?, ?)",
            (item_id, item["name"], item["price"], item["category"]),
        )

    conn.commit()
    conn.close()


def populate_users():
    """Populates the users table with initial users."""
    conn = get_user_db_connection()
    cursor = conn.cursor()

    users = [
        (1, "Connor"),
        (2, "Jacob"),
        (3, "Alex"),
        (4, "Matt"),
        (5, "Okezie"),
    ]
    cursor.executemany(
        "INSERT OR IGNORE INTO users (user_id, name) VALUES (?, ?)",
        users,
    )

    conn.commit()
    conn.close()