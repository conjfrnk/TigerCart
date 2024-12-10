#!/usr/bin/env python
"""
database.py
This module handles database connections and initialization.
"""
import sqlite3
import csv

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
            store_code TEXT PRIMARY KEY,
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
        phone_number TEXT,
        cart TEXT DEFAULT '{}',
        deliverer_rating_sum INTEGER DEFAULT 0,
        deliverer_rating_count INTEGER DEFAULT 0,
        shopper_rating_sum INTEGER DEFAULT 0,
        shopper_rating_count INTEGER DEFAULT 0,
        orders_placed INTEGER DEFAULT 0,
        items_purchased INTEGER DEFAULT 0,
        money_spent REAL DEFAULT 0.0,
        deliveries_completed INTEGER DEFAULT 0,
        items_delivered INTEGER DEFAULT 0,
        money_made REAL DEFAULT 0.0
    )
    """
)

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            user_id TEXT,
            item_id TEXT,
            PRIMARY KEY (user_id, item_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (item_id) REFERENCES items(store_code)
        )
        """
    )

    conn.commit()
    conn.close()


def populate_items_from_csv(filename="items.csv"):
    """Populates the items table with data from CSV file."""
    conn = get_main_db_connection()
    cursor = conn.cursor()

    # First, clear existing items
    cursor.execute("DELETE FROM items")

    current_category = None

    with open(filename, "r") as csvfile:
        csvreader = csv.reader(csvfile)
        next(csvreader)  # Skip header row

        for row in csvreader:
            # Skip empty rows
            if not any(row):
                continue

            # Check if this is a category row
            if row[0] and not any(row[1:]):
                current_category = row[0]
                continue

            # If we have a regular item row
            if row[0] and current_category and row[2]:  # Ensure store_code exists
                name = row[0]
                store_code = row[2]
                price = float(row[3]) if row[3] else 0.0

                cursor.execute(
                    """
                    INSERT INTO items
                    (store_code, name, price, category)
                    VALUES (?, ?, ?, ?)
                    """,
                    (store_code, name, price, current_category),
                )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_main_db()
    init_user_db()
    populate_items_from_csv()
