#!/usr/bin/env python
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
    Update the order's status to 'CLAIMED' and set the claimed_by field to the given user_id.

    :param user_id: The ID of the user claiming the order.
    :param delivery_id: The ID of the delivery (order) being claimed.
    """
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE orders SET status = 'CLAIMED', claimed_by = %s WHERE id = %s",
        (user_id, delivery_id),
    )
    conn.commit()
    conn.close()


def get_user_cart(user_id):
    """
    Fetch the cart for a given user.

    :param user_id: The ID of the user whose cart is fetched.
    :return: A dictionary containing the user's cart if it exists, otherwise None.
    """
    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT cart FROM users WHERE user_id = %s", (user_id,)
    )
    user = cursor.fetchone()
    conn.close()
    return user
