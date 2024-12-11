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
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE orders SET status = 'CLAIMED', claimed_by = %s WHERE id = %s",
        (user_id, delivery_id),
    )
    conn.commit()
    conn.close()


def get_user_cart(user_id):
    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT cart FROM users WHERE user_id = %s", (user_id,)
    )
    user = cursor.fetchone()
    conn.close()
    return user
