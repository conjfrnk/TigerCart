#!/usr/bin/env python
"""
server.py
Serves data for the TigerCart app.
Now using PostgreSQL and %s placeholders.
"""

import json
from flask import Flask, jsonify, request
from config import get_debug_mode, SECRET_KEY
from database import get_main_db_connection, get_user_db_connection
from db_utils import update_order_claim_status, get_user_cart

app = Flask(__name__)
app.secret_key = SECRET_KEY


@app.route("/items", methods=["GET"])
def get_items():
    """
    Fetch all items from the database and return them as a JSON response.

    :return: JSON with a dictionary of items keyed by their store_code.
    """
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM items")
    items = cursor.fetchall()
    conn.close()

    items_dict = {item["store_code"]: dict(item) for item in items}
    return jsonify(items_dict)


@app.route("/cart", methods=["GET", "POST"])
def manage_cart():
    """
    Manage the user's cart. GET returns the current cart. POST modifies the cart
    (add/update/delete items).

    Expected JSON payload for POST:
    {
        "user_id": "<user_id>",
        "item_id": "<item_id>",
        "action": "add" | "update" | "delete",
        "quantity": <int> (only required for update action)
    }

    :return: JSON representation of the user's updated cart.
    """
    data = request.json
    user_id = data.get("user_id")

    user = get_user_cart(user_id)
    if user is None:
        return jsonify({"error": "User not found"}), 404

    cart = json.loads(user["cart"]) if user["cart"] else {}

    if request.method == "POST":
        item_id = str(data.get("item_id"))
        action = data.get("action")

        # Check if item exists
        item_conn = get_main_db_connection()
        item_cursor = item_conn.cursor()
        item_cursor.execute("SELECT 1 FROM items WHERE store_code = %s", (item_id,))
        item_exists = item_cursor.fetchone()
        item_conn.close()

        if not item_exists:
            return jsonify({"error": "Item not found in inventory"}), 404

        # Modify cart
        if action == "add":
            cart[item_id] = {
                "quantity": cart.get(item_id, {}).get("quantity", 0) + 1
            }
        elif action == "delete":
            cart.pop(item_id, None)
        elif action == "update":
            quantity = data.get("quantity", 0)
            if quantity > 0:
                cart[item_id] = {"quantity": quantity}
            else:
                cart.pop(item_id, None)

        conn = get_user_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET cart = %s WHERE user_id = %s",
            (json.dumps(cart), user_id),
        )
        conn.commit()
        conn.close()

    return jsonify(cart)


def fetch_user_name(user_id, cursor_users):
    """
    Given a user_id and a database cursor for the users table, fetch the user's name.

    :param user_id: The ID of the user to look up.
    :param cursor_users: A cursor object for the users table.
    :return: The user's name or "Unknown User" if not found.
    """
    cursor_users.execute("SELECT name FROM users WHERE user_id = %s", (user_id,))
    user = cursor_users.fetchone()
    return user["name"] if user else "Unknown User"


def fetch_detailed_cart(cart, cursor_orders):
    """
    Given a cart dictionary and a database cursor for orders/items, return a detailed
    cart with item names, prices, and totals, as well as the subtotal.

    :param cart: A dictionary of item_id: {quantity: int} pairs.
    :param cursor_orders: A cursor for fetching item details.
    :return: (detailed_cart, subtotal) tuple.
    """
    detailed_cart = {}
    subtotal = 0
    for item_id, item_info in cart.items():
        cursor_orders.execute(
            "SELECT name, price FROM items WHERE store_code = %s",
            (item_id,),
        )
        item_data = cursor_orders.fetchone()
        if item_data:
            item_price = item_data["price"]
            quantity = item_info["quantity"]
            item_total = quantity * item_price
            subtotal += item_total
            detailed_cart[item_id] = {
                "name": item_data["name"],
                "price": item_price,
                "quantity": quantity,
                "total": item_total,
            }

    return detailed_cart, subtotal


@app.route("/deliveries", methods=["GET"])
def get_deliveries():
    """
    Get a list of deliveries available or currently claimed by a given deliverer.

    Expected JSON payload:
    {
        "user_id": "<deliverer_id>"
    }

    :return: JSON with a dictionary of deliveries keyed by their order ID.
    """
    deliverer_id = request.json.get("user_id")
    conn_orders = get_main_db_connection()
    cursor_orders = conn_orders.cursor()
    conn_users = get_user_db_connection()
    cursor_users = conn_users.cursor()

    cursor_orders.execute(
        """
        SELECT id, timestamp, user_id, total_items, cart, location, status, claimed_by
        FROM orders
        WHERE (status = 'PLACED' OR (status = 'CLAIMED' AND claimed_by = %s))
        AND status != 'DECLINED'
        """,
        (deliverer_id,),
    )
    orders = cursor_orders.fetchall()

    deliveries = {}
    for order in orders:
        user_name = fetch_user_name(order["user_id"], cursor_users)
        cart = json.loads(order["cart"])
        detailed_cart, subtotal = fetch_detailed_cart(cart, cursor_orders)
        earnings = round(subtotal * 0.1, 2)

        deliveries[str(order["id"])] = {
            "id": order["id"],
            "timestamp": order["timestamp"],
            "user_id": order["user_id"],
            "user_name": user_name,
            "total_items": order["total_items"],
            "cart": detailed_cart,
            "location": order["location"],
            "subtotal": round(subtotal, 2),
            "earnings": earnings,
        }

    conn_orders.close()
    conn_users.close()
    return jsonify(deliveries)


@app.route("/delivery/<delivery_id>", methods=["GET"])
def get_delivery(delivery_id):
    """
    Get details for a single delivery (order) by its ID.

    :param delivery_id: The ID of the delivery to fetch.
    :return: JSON of the delivery details or an error message if not found.
    """
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, timestamp, user_id, total_items, cart, location "
        "FROM orders WHERE id = %s",
        (delivery_id,),
    )
    order = cursor.fetchone()

    if order:
        cart_data = json.loads(order["cart"])
        detailed_cart, subtotal = fetch_detailed_cart(cart_data, cursor)
        earnings = round(subtotal * 0.1, 2)

        delivery = {
            "id": order["id"],
            "timestamp": order["timestamp"],
            "user_id": order["user_id"],
            "total_items": order["total_items"],
            "cart": detailed_cart,
            "location": order["location"],
            "subtotal": round(subtotal, 2),
            "earnings": earnings,
        }
        conn.close()
        return jsonify(delivery)

    conn.close()
    return jsonify({"error": "Delivery not found"}), 404


@app.route("/accept_delivery/<delivery_id>", methods=["POST"])
def accept_delivery_route(delivery_id):
    """
    Accept a delivery for a given user (claim it).

    Expected JSON payload:
    {
        "user_id": "<deliverer_id>"
    }

    :param delivery_id: The ID of the delivery to accept.
    :return: JSON success message if successful.
    """
    user_id = request.json.get("user_id")
    update_order_claim_status(user_id, delivery_id)
    return jsonify({"success": True}), 200


@app.route("/decline_delivery/<delivery_id>", methods=["POST"])
def decline_delivery(delivery_id):
    """
    Decline a previously available delivery.

    :param delivery_id: The ID of the delivery to decline.
    :return: JSON success message if successful.
    """
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE orders SET status = 'DECLINED' WHERE id = %s",
        (delivery_id,),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True}), 200


@app.route("/get_shopper_timeline", methods=["GET"])
def get_shopper_timeline():
    """
    Get the timeline for a given shopper's order based on order_id query parameter.

    :queryparam order_id: The ID of the order for which the timeline is requested.
    :return: JSON with the timeline of the order or an error if not found.
    """
    order_id = request.args.get("order_id")
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT timeline FROM orders WHERE id = %s", (order_id,)
    )
    timeline_status = cursor.fetchone()
    conn.close()

    if timeline_status:
        return jsonify(timeline=timeline_status["timeline"]), 200

    return jsonify({"error": "Order not found"}), 404


if __name__ == "__main__":
    app.run(port=5150, debug=get_debug_mode())
