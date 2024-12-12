#!/usr/bin/env python
"""
database.py
This module handles database connections and initialization for PostgreSQL.
It reads DB credentials from secrets.txt and constructs a DATABASE_URL.
"""

import os
import csv
import psycopg2
from psycopg2.extras import RealDictCursor


def load_secrets(filename="secrets.txt"):
    """
    Load database-related secrets from a file.

    :param filename: The name of the secrets file.
    :return: A dictionary of secrets read from the file.
    :raises FileNotFoundError: If the secrets file doesn't exist.
    """
    loaded_secrets = {}
    if not os.path.exists(filename):
        raise FileNotFoundError(f"Secrets file '{filename}' not found.")
    # Specify encoding to avoid W1514 warning
    with open(filename, "r", encoding="utf-8") as secret_file:
        for line in secret_file:
            line = line.strip()
            if line and "=" in line:
                key, val = line.split("=", 1)
                loaded_secrets[key.strip()] = val.strip()
    return loaded_secrets


# Use a different variable name to avoid redefinition warning
app_secrets = load_secrets()
DB_USER = app_secrets.get("DB_USER", "app_user")
DB_PASS = app_secrets.get("DB_PASS", "password")
DB_HOST = app_secrets.get("DB_HOST", "localhost")
DB_PORT = app_secrets.get("DB_PORT", "5432")
DB_NAME = app_secrets.get("DB_NAME", "app_db")

DATABASE_URL = (
    f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)


def get_main_db_connection():
    """
    Establish and return a connection to the main database.

    :return: A psycopg2 connection object with RealDictCursor.
    """
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn


def get_user_db_connection():
    """
    For this app, main and user database are the same Postgres DB.
    Returns a connection to the same DB.

    :return: A psycopg2 connection object with RealDictCursor.
    """
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn


def init_user_db():
    """
    Initializes the user database with the necessary tables.
    Creates 'users' table if it does not exist.
    """
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
            shopper_rating_count INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def init_main_db():
    """
    Initializes the main database with necessary tables (items, orders)
    and performs database migrations as needed.
    Must run after init_user_db().
    """
    conn = get_main_db_connection()
    cursor = conn.cursor()

    # Create items table
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

    # Create orders table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            status TEXT CHECK (status IN ('PLACED', 'CLAIMED', 'FULFILLED',
                                          'DECLINED', 'CANCELLED')),
            timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
            user_id TEXT,
            total_items INTEGER,
            cart TEXT,
            location TEXT,
            timeline TEXT DEFAULT '{}',
            claimed_by TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
        """
    )

    # Database migration: Add shopper_rated and deliverer_rated columns if not exist
    cursor.execute(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS shopper_rated BOOLEAN DEFAULT FALSE"
    )
    cursor.execute(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS deliverer_rated BOOLEAN DEFAULT FALSE"
    )

    conn.commit()
    conn.close()


def create_favorites_table():
    """
    Creates the favorites table if it doesn't exist.
    This table links users and items to store their favorites.
    """
    conn = get_main_db_connection()
    cursor = conn.cursor()
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
    """
    Populates the items table with data from a CSV file.
    The CSV may contain categories and items.

    :param filename: The CSV filename containing item data.
    """
    conn = get_main_db_connection()
    cursor = conn.cursor()

    # Clear existing items
    cursor.execute("DELETE FROM items")

    if not os.path.exists(filename):
        conn.commit()
        conn.close()
        return

    current_category = None
    # Specify encoding to avoid W1514 warning
    with open(filename, "r", encoding="utf-8") as csvfile:
        csvreader = csv.reader(csvfile)
        for row in csvreader:
            if not any(row):
                continue
            if row[0] and not any(row[1:]):
                # This is a category row
                current_category = row[0]
                continue

            if len(row) >= 4 and row[2]:
                name = row[0]
                store_code = row[2]
                price_str = row[3] if row[3] else "0.0"
                try:
                    price = float(price_str)
                except ValueError:
                    price = 0.0

                if current_category:
                    cursor.execute(
                        """
                        INSERT INTO items (store_code, name, price, category)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (store_code) DO NOTHING
                        """,
                        (store_code, name, price, current_category),
                    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_user_db()
    init_main_db()
    populate_items_from_csv()
    create_favorites_table()
