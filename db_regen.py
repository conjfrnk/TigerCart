#!/usr/bin/env python
"""
db_regen.py
Clears the database and regenerates it by running initialization code from database.py.
"""

from database import (
    get_main_db_connection,
    get_user_db_connection,
    init_user_db,
    init_main_db,
    create_favorites_table,
    populate_items_from_csv,
)


def drop_all_tables():
    """
    Drops all known tables from the database to start fresh.
    """
    # We'll connect to the main DB since both main and user share the same one in this setup
    conn = get_main_db_connection()
    cursor = conn.cursor()

    # Drop tables that we know exist (in reverse order of dependencies)
    # favorites depends on users and items
    cursor.execute("DROP TABLE IF EXISTS favorites")
    # orders depends on users
    cursor.execute("DROP TABLE IF EXISTS orders")
    # items is standalone
    cursor.execute("DROP TABLE IF EXISTS items")
    # users is standalone
    cursor.execute("DROP TABLE IF EXISTS users")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    drop_all_tables()
    init_user_db()
    init_main_db()
    populate_items_from_csv()
    create_favorites_table()
    print("Database has been cleared and regenerated successfully.")
