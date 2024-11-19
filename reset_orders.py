#!/usr/bin/env python
"""Reset orders module for clearing the orders database table.

This module provides functionality to reset the orders database
by removing all existing orders.
"""

from database import get_main_db_connection


def reset_orders():
    """Delete all orders from the database.

    This function connects to the main database and removes all entries
    from the orders table. Use with caution as this operation cannot
    be undone.

    Returns:
        None
    """
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM orders")
    conn.commit()
    conn.close()
    print("All orders have been deleted successfully.")


if __name__ == "__main__":
    reset_orders()
