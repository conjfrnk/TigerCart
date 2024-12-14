#!/usr/bin/env python
"""
server.py
Serves data for the TigerCart app.
Now using PostgreSQL and %s placeholders and improved security checks.
"""

import json
import logging
from flask import Flask, jsonify, request, session
from config import get_debug_mode, SECRET_KEY
from database import get_main_db_connection, get_user_db_connection
from db_utils import update_order_claim_status, get_user_cart

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Get items from the database
@app.route("/items", methods=["GET"])
def get_items():
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM items")
    items = cursor.fetchall()
    conn.close()

    items_dict = {item["store_code"]: dict(item) for item in items}
    return jsonify(items_dict)

@app.route("/cart", methods=["GET", "POST"])
def manage_cart():
    if request.method == "GET":
        # For GET requests, get user_id from query parameters
        user_id = request.args.get("user_id")
        if not user_id:
            return jsonify({"error": "User ID is required"}), 400

        user = get_user_cart(user_id)
        if user is None:
            return jsonify({"error": "User not found"}), 404

        cart = json.loads(user["cart"]) if user["cart"] else {}
        return jsonify(cart)

    # POST request: expect JSON with user_id, item_id, action
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid request"}), 400

    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "User ID is required"}), 400

    user = get_user_cart(user_id)
    if user is None:
        return jsonify({"error": "User not found"}), 404

    cart = json.loads(user["cart"]) if user["cart"] else {}

    item_id = data.get("item_id")
    action = data.get("action")

    if not item_id or not action:
        return jsonify({"error": "item_id and action required"}), 400

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
        cart[item_id] = {"quantity": cart.get(item_id, {}).get("quantity", 0) + 1}
    elif action == "delete":
        cart.pop(item_id, None)
    elif action == "update":
        quantity = data.get("quantity", 0)
        if quantity > 0:
            cart[item_id] = {"quantity": quantity}
        else:
            cart.pop(item_id, None)
    else:
        return jsonify({"error": "Invalid action"}), 400

    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET cart = %s WHERE user_id = %s",
        (json.dumps(cart), user_id),
    )
    conn.commit()
    conn.close()

    return jsonify(cart)


# Get deliveries
@app.route("/deliveries", methods=["GET"])
def get_deliveries():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid request"}), 400
    deliverer_id = data.get("user_id")

    # Security check
    session_user_id = session.get("user_id")
    if not session_user_id or session_user_id != deliverer_id:
        return jsonify({"error": "Unauthorized"}), 403

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
        detailed_cart, subtotal = fetch_detailed_cart(
            cart, cursor_orders
        )
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

# Get delivery
@app.route("/delivery/<delivery_id>", methods=["GET"])
def get_delivery(delivery_id):
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, timestamp, user_id, total_items, cart, location FROM orders WHERE id = %s",
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

# Accept delivery
@app.route("/accept_delivery/<delivery_id>", methods=["POST"])
def accept_delivery_route(delivery_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid request"}), 400
    user_id = data.get("user_id")

    # Security check
    session_user_id = session.get("user_id")
    if not session_user_id or session_user_id != user_id:
        return jsonify({"error": "Unauthorized"}), 403

    update_order_claim_status(user_id, delivery_id)
    return jsonify({"success": True}), 200

# Decline delivery
@app.route("/decline_delivery/<delivery_id>", methods=["POST"])
def decline_delivery(delivery_id):
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE orders SET status = 'DECLINED' WHERE id = %s",
        (delivery_id,),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True}), 200

# Get shopper timeline
@app.route("/get_shopper_timeline", methods=["GET"])
def get_shopper_timeline():
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

# Get user cart
def fetch_user_name(user_id, cursor_users):
    cursor_users.execute(
        "SELECT name FROM users WHERE user_id = %s", (user_id,)
    )
    user = cursor_users.fetchone()
    return user["name"] if user else "Unknown User"
# Fetch detailed cart
def fetch_detailed_cart(cart, cursor_orders):
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

if __name__ == "__main__":
    app.run(port=5150, debug=get_debug_mode())