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
    secrets = {}
    if not os.path.exists(filename):
        raise FileNotFoundError(f"Secrets file '{filename}' not found.")
    with open(filename, "r") as f:
        for line in f:
            line = line.strip()
            if line and '=' in line:
                key, val = line.split('=', 1)
                secrets[key.strip()] = val.strip()
    return secrets

secrets = load_secrets()
DB_USER = secrets.get("DB_USER", "app_user")
DB_PASS = secrets.get("DB_PASS", "password")
DB_HOST = secrets.get("DB_HOST", "localhost")
DB_PORT = secrets.get("DB_PORT", "5432")
DB_NAME = secrets.get("DB_NAME", "app_db")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

def get_main_db_connection():
    """Establishes and returns a connection to the main database."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def get_user_db_connection():
    """For this app, main and user database are the same Postgres DB."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
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
            id SERIAL PRIMARY KEY,
            status TEXT CHECK (status IN ('PLACED', 'CLAIMED', 'FULFILLED', 'DECLINED', 'CANCELLED')),
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
            shopper_rating_count INTEGER DEFAULT 0
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

    # Clear existing items
    cursor.execute("DELETE FROM items")

    current_category = None

    with open(filename, "r") as csvfile:
        csvreader = csv.reader(csvfile)
        next(csvreader)  # Skip header row

        for row in csvreader:
            if not any(row):
                continue
            # Check if this is a category row
            if row[0] and not any(row[1:]):
                current_category = row[0]
                continue

            # If we have a regular item row
            if row[0] and current_category and len(row) > 3 and row[2]:
                name = row[0]
                store_code = row[2]
                price = float(row[3]) if row[3] else 0.0

                cursor.execute(
                    """
                    INSERT INTO items (store_code, name, price, category)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (store_code, name, price, current_category),
                )

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_main_db()
    init_user_db()
    populate_items_from_csv()
