#!/usr/bin/env python
# db_utils.py
"""
db_utils.py
Database utility functions for shared database operations.
"""

from typing import Union
from database import get_main_db_connection, get_user_db_connection


def update_order_claim_status(
    user_id: Union[str, int], delivery_id: Union[str, int]
) -> None:
    """
    Update the claim status of an order in the database.

    Args:
        user_id: The ID of the user claiming the order
        delivery_id: The ID of the delivery/order being claimed
    """
    conn = get_main_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "UPDATE orders SET status =\
            'CLAIMED', claimed_by = ? WHERE id = ?",
            (user_id, delivery_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_user_cart(user_id):
    """Fetches the cart for a user by user_id."""
    conn = get_user_db_connection()
    cursor = conn.cursor()
    user = cursor.execute(
        "SELECT cart FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return user
