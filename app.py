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
from db_utils import update_order_claim_status

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


@app.route("/shopper_timeline")
def shopper_timeline():
    """Display the shopper's order timeline."""
    username = authenticate()
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("home"))

    conn = get_main_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1",
        (user_id,),
    )
    order = cursor.fetchone()

    deliverer_venmo = None
    if order and order["claimed_by"]:
        user_conn = get_user_db_connection()
        user_cursor = user_conn.cursor()
        user_cursor.execute(
            "SELECT venmo_handle FROM users WHERE user_id = ?",
            (order["claimed_by"],),
        )
        deliverer = user_cursor.fetchone()
        if deliverer:
            deliverer_venmo = deliverer["venmo_handle"]
        user_conn.close()

    conn.close()

    if not order:
        return "No orders found."

    order_dict = dict(order)
    order_dict["timeline"] = json.loads(
        order_dict.get("timeline", "{}")
    )
    order_dict["cart"] = json.loads(order_dict.get("cart", "{}"))

    return render_template(
        "shopper_timeline.html",
        order=order_dict,
        deliverer_venmo=deliverer_venmo,
        username=username,
    )


@app.route("/category_view/<category>")
def category_view(category):
    """Display items in a specific category."""
    username = authenticate()
    response = requests.get(
        f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT
    )
    sample_items = response.json()
    items_in_category = {
        k: v
        for k, v in sample_items.items()
        if v.get("category") == category
    }
    return render_template(
        "category_view.html",
        category=category,
        items=items_in_category,
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


@app.route("/delete_item/<item_id>", methods=["POST"])
def delete_item(item_id):
    """Delete an item from the cart."""
    response = requests.post(
        f"{SERVER_URL}/cart",
        json={
            "user_id": session["user_id"],
            "item_id": item_id,
            "action": "delete",
        },
        timeout=REQUEST_TIMEOUT,
    )
    return jsonify(response.json())


@app.route("/update_cart/<item_id>/<action>", methods=["POST"])
def update_cart(item_id, action):
    """Update the cart by increasing or decreasing item quantities."""
    user_id = session["user_id"]
    if action == "increase":
        requests.post(
            f"{SERVER_URL}/cart",
            json={
                "user_id": user_id,
                "item_id": item_id,
                "action": "add",
            },
            timeout=REQUEST_TIMEOUT,
        )
    elif action == "decrease":
        cart_response = requests.get(
            f"{SERVER_URL}/cart",
            json={"user_id": user_id},
            timeout=REQUEST_TIMEOUT,
        )
        cart = cart_response.json()
        quantity = cart.get(item_id, {}).get("quantity", 0)
        if quantity > 1:
            requests.post(
                f"{SERVER_URL}/cart",
                json={
                    "user_id": user_id,
                    "item_id": item_id,
                    "quantity": quantity - 1,
                    "action": "update",
                },
                timeout=REQUEST_TIMEOUT,
            )
        elif quantity == 1:
            requests.post(
                f"{SERVER_URL}/cart",
                json={
                    "user_id": user_id,
                    "item_id": item_id,
                    "action": "delete",
                },
                timeout=REQUEST_TIMEOUT,
            )
    return jsonify({"success": True})


@app.route("/order_status/<int:order_id>")
def order_status(order_id):
    """Return the timeline status of an order in JSON format."""
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT timeline FROM orders WHERE id = ?", (order_id,)
    )
    order = cursor.fetchone()
    conn.close()

    if not order:
        return jsonify({"error": "Order not found."}), 404

    timeline = json.loads(order["timeline"])
    return jsonify({"timeline": timeline})


@app.route("/order_confirmation")
def order_confirmation():
    """Display the order confirmation page with items in cart."""
    username = authenticate()
    response = requests.get(
        f"{SERVER_URL}/cart",
        json={"user_id": session["user_id"]},
        timeout=REQUEST_TIMEOUT,
    )
    items_in_cart = len(response.json())
    return render_template(
        "order_confirmation.html",
        items_in_cart=items_in_cart,
        username=username,
    )


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


@app.route("/deliver")
def deliver():
    """Display available deliveries for deliverers."""
    current_username = authenticate()
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("home"))

    conn = get_main_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM orders WHERE status = 'placed'")
    available_deliveries = cursor.fetchall()

    cursor.execute(
        "SELECT * FROM orders WHERE status = 'claimed' AND claimed_by = ?",
        (user_id,),
    )
    my_deliveries = cursor.fetchall()

    available_deliveries = [
        dict(delivery) for delivery in available_deliveries
    ]
    my_deliveries = [dict(delivery) for delivery in my_deliveries]

    for delivery in available_deliveries + my_deliveries:
        cart = json.loads(delivery["cart"])
        subtotal = sum(
            item["quantity"] * item["price"] for item in cart.values()
        )
        delivery["earnings"] = round(
            subtotal * DELIVERY_FEE_PERCENTAGE, 2
        )

    conn.close()

    return render_template(
        "deliver.html",
        available_deliveries=available_deliveries,
        my_deliveries=my_deliveries,
        username=current_username,
    )


@app.route("/delivery/<delivery_id>")
def delivery_details(delivery_id):
    """Display details of a specific delivery."""
    current_username = authenticate()
    response = requests.get(
        f"{SERVER_URL}/delivery/{delivery_id}", timeout=REQUEST_TIMEOUT
    )
    if response.status_code == 200:
        delivery = response.json()
        return render_template(
            "delivery_details.html",
            delivery=delivery,
            username=current_username,
        )
    return "Delivery not found", 404


@app.route("/accept_delivery/<int:delivery_id>", methods=["POST"])
def accept_delivery(delivery_id):
    """Mark the delivery as accepted by changing its status."""
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    update_order_claim_status(user_id, delivery_id)

    return redirect(
        url_for("delivery_timeline", delivery_id=delivery_id)
    )


@app.route("/decline_delivery/<delivery_id>", methods=["POST"])
def decline_delivery(delivery_id):
    """Decline a delivery by forwarding the request to the backend server."""
    response = requests.post(
        f"{SERVER_URL}/decline_delivery/{delivery_id}",
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code == 200:
        return redirect(url_for("deliver"))
    return "Error declining delivery", response.status_code


@app.route("/update_checklist", methods=["POST"])
def update_checklist():
    """Update the order's timeline based on deliverer's actions."""
    data = request.get_json()
    order_id = data.get("order_id")
    step = data.get("step")
    checked = data.get("checked")

    user_id = session.get("user_id")
    if not user_id:
        return (
            jsonify({"success": False, "error": "User not logged in"}),
            401,
        )

    conn = get_main_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT timeline, claimed_by FROM orders WHERE id = ?",
        (order_id,),
    )
    order = cursor.fetchone()

    if not order:
        conn.close()
        return (
            jsonify({"success": False, "error": "Order not found"}),
            404,
        )

    if order["claimed_by"] != user_id:
        conn.close()
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Not authorized to update this order",
                }
            ),
            403,
        )

    timeline = json.loads(order["timeline"])
    steps = [
        "Order Accepted",
        "Venmo Payment Received",
        "Shopping in U-Store",
        "Checked Out",
        "On Delivery",
        "Delivered",
    ]
    step_index = steps.index(step)

    if checked:
        if step_index > 0:
            previous_step = steps[step_index - 1]
            if not timeline.get(previous_step, False):
                conn.close()
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f'Previous step "{previous_step}" must be completed first.',
                        }
                    ),
                    400,
                )
    else:
        if any(
            timeline.get(steps[i], False)
            for i in range(step_index + 1, len(steps))
        ):
            conn.close()
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Cannot uncheck this step because subsequent steps are completed.",
                    }
                ),
                400,
            )

    # Update the timeline
    timeline[step] = checked
    cursor.execute(
        "UPDATE orders SET timeline = ? WHERE id = ?",
        (json.dumps(timeline), order_id),
    )
    conn.commit()
    conn.close()

    # Return the updated timeline
    return jsonify({"success": True, "timeline": timeline}), 200



@app.route("/profile")
def profile():
    """Display the user's profile, order history, and statistics."""
    current_username = authenticate()
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user_id = session["user_id"]
    user_profile = get_user_data(user_id)
    orders = get_user_orders(user_id)
    stats = calculate_user_stats(orders)

    orders_with_totals = []
    for order in orders:
        cart = json.loads(order["cart"])
        subtotal = sum(
            details.get("quantity", 0) * details.get("price", 0)
            for item_id, details in cart.items()
        )
        order_data = dict(order)
        order_data["total"] = round(subtotal, 2)
        orders_with_totals.append(order_data)

    return render_template(
        "profile.html",
        username=current_username,
        orders=orders_with_totals,
        stats=stats,
        user_profile=user_profile,
    )


@app.route("/add_favorite/<item_id>", methods=["POST"])
def add_favorite(item_id):
    """Add an item to the user's favorites."""
    user_id = session["user_id"]
    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO favorites (user_id, item_id) VALUES (?, ?)",
        (user_id, item_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/remove_favorite/<item_id>", methods=["POST"])
def remove_favorite(item_id):
    """Remove an item from the user's favorites."""
    user_id = session["user_id"]
    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM favorites WHERE user_id = ? AND item_id = ?",
        (user_id, item_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/delivery_timeline/<int:delivery_id>")
def delivery_timeline(delivery_id):
    """Display the delivery timeline for a specific delivery."""
    current_username = authenticate()
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    conn = get_main_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM orders WHERE id = ?", (delivery_id,))
    order_row = cursor.fetchone()

    shopper_venmo = None
    if order_row:
        user_conn = get_user_db_connection()
        user_cursor = user_conn.cursor()
        user_cursor.execute(
            "SELECT venmo_handle FROM users WHERE user_id = ?",
            (order_row["user_id"],),
        )
        shopper = user_cursor.fetchone()
        if shopper:
            shopper_venmo = shopper["venmo_handle"]
        user_conn.close()

    conn.close()

    if not order_row:
        return "Order not found.", 404

    order = dict(order_row)
    order["timeline"] = json.loads(order.get("timeline", "{}"))
    order["cart"] = json.loads(order.get("cart", "{}"))

    return render_template(
        "deliverer_timeline.html",
        order=order,
        shopper_venmo=shopper_venmo,
        username=current_username,
    )


@app.route("/order_details/<int:order_id>")
def order_details(order_id):
    """Display details of a specific order."""
    conn = get_main_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    order_row = cursor.fetchone()
    conn.close()

    if not order_row:
        return "Order not found.", 404

    order = dict(order_row)
    order["cart"] = json.loads(order.get("cart", "{}"))

    return render_template("order_details.html", order=order)


def get_user_data(user_id):
    """Fetch user data from the database."""
    conn = get_user_db_connection()
    cursor = conn.cursor()
    user = cursor.execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return user


def get_user_orders(user_id):
    """Fetch all orders made by the user."""
    conn = get_main_db_connection()
    cursor = conn.cursor()
    orders = cursor.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY timestamp DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return orders


def calculate_user_stats(orders):
    """Calculate statistics based on the user's orders."""
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
