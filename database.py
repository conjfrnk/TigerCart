#!/usr/bin/env python
"""
database.py
Populates tigercart.sqlite3 and users.sqlite3
"""

import sqlite3

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
            status TEXT CHECK(status IN
            ('PLACED', 'CLAIMED', 'FULFILLED', 'DECLINED', 'CANCELLED')),
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id TEXT,
            total_items INTEGER,
            cart TEXT,
            location TEXT,
            timeline TEXT DEFAULT '{}',
            claimed_by INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
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
            user_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            venmo_handle TEXT,
            cart TEXT DEFAULT '{}'
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            user_id TEXT,
            item_id INTEGER,
            PRIMARY KEY (user_id, item_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (item_id) REFERENCES items(id)
        )
        """
    )

    conn.commit()
    conn.close()


def populate_items():
    """Populates the items table with sample data."""
    conn = get_main_db_connection()
    cursor = conn.cursor()

    sample_items = {
        "1": {"name": "Coke", "price": 1.09, "category": "drinks"},
        "2": {"name": "Diet Coke", "price": 1.29, "category": "drinks"},
        "3": {
            "name": "Tropicana Orange Juice",
            "price": 0.89,
            "category": "drinks",
        },
        "4": {
            "name": "Lay's Potato Chips",
            "price": 1.59,
            "category": "food",
        },
        "5": {
            "name": "Snickers Bar",
            "price": 0.99,
            "category": "food",
        },
        "6": {"name": "Notebook", "price": 2.49, "category": "other"},
    }

    for item_id, item in sample_items.items():
        cursor.execute(
            "INSERT OR IGNORE INTO items (id, name, price, category) VALUES (?, ?, ?, ?)",
            (item_id, item["name"], item["price"], item["category"]),
        )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_main_db()
    init_user_db()
    populate_items()
