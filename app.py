#!/usr/bin/env python
"""
app.py
Authors: TigerCart team
"""

import json
import requests
from flask import (
    Flask,
    render_template,
    redirect,
    url_for,
    request,
    session,
    jsonify,
    flash,
)
from config import get_debug_mode, SECRET_KEY
from database import (
    get_main_db_connection,
    get_user_db_connection,
    init_user_db,
)
from auth import auth_bp, authenticate

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.register_blueprint(auth_bp)

SERVER_URL = "http://localhost:5150"
REQUEST_TIMEOUT = 5
DELIVERY_FEE_PERCENTAGE = 0.1


@app.route("/", methods=["GET"])
@app.route("/index", methods=["GET"])
def home():
    """Display the home page and initialize user if needed."""
    username = authenticate()
    session["user_id"] = username

    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, name) VALUES (?, ?)",
        (session["user_id"], username),
    )
    conn.commit()
    conn.close()

    return render_template("home.html", username=username)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    """Handle user settings page for updating Venmo handle."""
    username = authenticate()
    user_id = username
    conn = get_user_db_connection()
    cursor = conn.cursor()

    if request.method == "POST":
        venmo_handle = request.form.get("venmo_handle")
        cursor.execute(
            "UPDATE users SET venmo_handle = ? WHERE user_id = ?",
            (venmo_handle, user_id),
        )
        conn.commit()
        conn.close()
        flash("Venmo handle updated successfully.")
        return redirect(url_for("settings"))

    user = cursor.execute(
        "SELECT venmo_handle FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return render_template(
        "settings.html",
        venmo_handle=user["venmo_handle"] if user else "",
        username=username,
    )


@app.route("/shop")
def shop():
    """Display items available in the shop and current order if any."""
    username = authenticate()
    response = requests.get(
        f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT
    )
    sample_items = response.json()

    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM orders 
        WHERE user_id = ? AND status IN ('placed', 'claimed') 
        ORDER BY timestamp DESC LIMIT 1""",
        (user_id,),
    )
    current_order = cursor.fetchone()
    conn.close()

    return render_template(
        "shop.html",
        items=sample_items,
        current_order=current_order,
        username=username,
    )


@app.route("/cart_view")
def cart_view():
    """Display the cart view with item subtotals and total cost."""
    username = authenticate()
    if "user_id" not in session:
        return redirect(url_for("home"))

    items_response = requests.get(
        f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT
    )
    cart_response = requests.get(
        f"{SERVER_URL}/cart",
        json={"user_id": session["user_id"]},
        timeout=REQUEST_TIMEOUT,
    )

    sample_items = items_response.json()
    cart = cart_response.json()

    subtotal = sum(
        details.get("quantity", 0)
        * sample_items.get(item_id, {}).get("price", 0)
        for item_id, details in cart.items()
        if isinstance(details, dict)
    )
    delivery_fee = round(subtotal * DELIVERY_FEE_PERCENTAGE, 2)
    total = round(subtotal + delivery_fee, 2)

    return render_template(
        "cart_view.html",
        cart=cart,
        items=sample_items,
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        total=total,
        username=username,
    )


@app.route("/add_to_cart/<item_id>", methods=["POST"])
def add_to_cart(item_id):
    """Add an item to the cart."""
    response = requests.post(
        f"{SERVER_URL}/cart",
        json={
            "user_id": session["user_id"],
            "item_id": item_id,
            "action": "add",
        },
        timeout=REQUEST_TIMEOUT,
    )
    return jsonify(response.json())


@app.route("/place_order", methods=["POST"])
def place_order():
    """Place an order and clear the user's cart."""
    user_id = session.get("user_id")
    data = request.get_json()
    delivery_location = data.get("delivery_location")

    if not delivery_location:
        return jsonify({"error": "Delivery location is required"}), 400

    user_conn = get_user_db_connection()
    user_cursor = user_conn.cursor()
    user = user_cursor.execute(
        "SELECT cart FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    cart = json.loads(user["cart"]) if user and user["cart"] else {}

    if not cart:
        return jsonify({"error": "Cart is empty"}), 400

    items_response = requests.get(
        f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT
    )
    items = items_response.json()

    for item_id in cart:
        item = items.get(item_id)
        if item:
            cart[item_id]["price"] = item["price"]
            cart[item_id]["name"] = item["name"]

    total_items = sum(details["quantity"] for details in cart.values())
    conn = get_main_db_connection()
    cursor = conn.cursor()

    timeline = {
        "Order Accepted": False,
        "Venmo Payment Received": False,
        "Shopping in U-Store": False,
        "Checked Out": False,
        "On Delivery": False,
        "Delivered": False,
    }

    cursor.execute(
        """INSERT INTO orders
        (status, user_id, total_items, cart, location, timeline)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (
            "placed",
            user_id,
            total_items,
            json.dumps(cart),
            delivery_location,
            json.dumps(timeline),
        ),
    )

    user_cursor.execute(
        "UPDATE users SET cart = '{}' WHERE user_id = ?", (user_id,)
    )
    conn.commit()
    user_conn.commit()
    conn.close()
    user_conn.close()

    return jsonify({"success": True}), 200


def get_user_data(user_id):
    """Fetch user data from the database.

    Args:
        user_id: The ID of the user to fetch data for

    Returns:
        dict: User data from the database
    """
    conn = get_user_db_connection()
    cursor = conn.cursor()
    user = cursor.execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return user


def get_user_orders(user_id):
    """Fetch all orders made by the user.

    Args:
        user_id: The ID of the user to fetch orders for

    Returns:
        list: List of orders from the database
    """
    conn = get_main_db_connection()
    cursor = conn.cursor()
    orders = cursor.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY timestamp DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return orders


def calculate_user_stats(orders):
    """Calculate statistics based on the user's orders.

    Args:
        orders: List of user orders to calculate stats from

    Returns:
        dict: Statistics including total orders, spent amount, and items
    """
    total_spent = 0
    total_items = 0

    for order in orders:
        total_items += order["total_items"]
        cart = json.loads(order["cart"])
        subtotal = sum(
            details.get("quantity", 0) * details.get("price", 0)
            for details in cart.values()
        )
        total_spent += subtotal

    return {
        "total_orders": len(orders),
        "total_spent": round(total_spent, 2),
        "total_items": total_items,
    }


if __name__ == "__main__":
    init_user_db()
    app.run(host="localhost", port=8000, debug=get_debug_mode())
