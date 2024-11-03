#!/usr/bin/env python
"""
app.py
Authors: TigerCart team
"""

from flask import (
    Flask,
    render_template,
    redirect,
    url_for,
    request,
    session,
    jsonify,
)
import requests
import json
from config import get_debug_mode
from database import get_main_db_connection, get_user_db_connection

app = Flask(__name__)
app.secret_key = (
    "your_secret_key"  # Set a secure secret key for sessions
)

# Define the base URL for the server
SERVER_URL = "http://localhost:5150"
REQUEST_TIMEOUT = 5  # Timeout in seconds for all requests
DELIVERY_FEE_PERCENTAGE = 0.1


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user_id = request.form.get("user_id")
        session["user_id"] = user_id
        user = (
            get_user_db_connection()
            .execute(
                "SELECT name FROM users WHERE user_id = ?", (user_id,)
            )
            .fetchone()
        )
        session["user_name"] = user["name"] if user else "Guest"
        return redirect(url_for("home"))

    users = (
        get_user_db_connection()
        .execute("SELECT * FROM users")
        .fetchall()
    )
    return render_template("login.html", users=users)


@app.route("/logout", methods=["POST"])
def logout():
    session["user_id"] = None
    return "", 204


@app.route("/")
def home():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("home.html")


@app.route("/shop")
def shop():
    response = requests.get(
        f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT
    )
    sample_items = response.json()
    return render_template("shop.html", items=sample_items)


@app.route("/category_view/<category>")
def category_view(category):
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
        "category_view.html", category=category, items=items_in_category
    )


@app.route("/cart_view")
def cart_view():
    items_response = requests.get(
        f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT
    )
    cart_response = requests.get(
        f"{SERVER_URL}/cart", timeout=REQUEST_TIMEOUT
    )

    sample_items = items_response.json()
    cart = cart_response.json()

    # Safely calculate subtotal by ensuring each item in cart has a "quantity"
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
    )


@app.route("/add_to_cart/<item_id>", methods=["POST"])
def add_to_cart(item_id):
    response = requests.post(
        f"{SERVER_URL}/cart",
        json={"item_id": item_id, "action": "add"},
        timeout=REQUEST_TIMEOUT,
    )
    return jsonify(response.json())


@app.route("/delete_item/<item_id>", methods=["POST"])
def delete_item(item_id):
    response = requests.post(
        f"{SERVER_URL}/cart",
        json={"item_id": item_id, "action": "delete"},
        timeout=REQUEST_TIMEOUT,
    )
    return jsonify(response.json())


@app.route("/update_cart/<item_id>/<action>", methods=["POST"])
def update_cart(item_id, action):
    response = requests.get(
        f"{SERVER_URL}/cart", timeout=REQUEST_TIMEOUT
    )
    cart = response.json()

    if action == "increase":
        requests.post(
            f"{SERVER_URL}/cart",
            json={"item_id": item_id, "action": "add"},
            timeout=REQUEST_TIMEOUT,
        )
    elif action == "decrease":
        quantity = cart.get(item_id, {}).get("quantity", 0)
        if quantity > 1:
            requests.post(
                f"{SERVER_URL}/cart",
                json={
                    "item_id": item_id,
                    "quantity": quantity - 1,
                    "action": "update",
                },
                timeout=REQUEST_TIMEOUT,
            )
        elif quantity == 1:
            requests.post(
                f"{SERVER_URL}/cart",
                json={"item_id": item_id, "action": "delete"},
                timeout=REQUEST_TIMEOUT,
            )
    return jsonify(cart)


@app.route("/order_confirmation")
def order_confirmation():
    response = requests.get(
        f"{SERVER_URL}/cart", timeout=REQUEST_TIMEOUT
    )
    items_in_cart = len(response.json())
    return render_template(
        "order_confirmation.html", items_in_cart=items_in_cart
    )


@app.route("/place_order", methods=["POST"])
def place_order():
    conn = get_main_db_connection()
    user_conn = get_user_db_connection()
    cursor = conn.cursor()
    user_cursor = user_conn.cursor()

    user_id = session.get("user_id")
    delivery_location = request.form.get("delivery_location")

    # Retrieve the user's cart data
    user = user_cursor.execute(
        "SELECT cart FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    cart = json.loads(user["cart"]) if user and user["cart"] else {}

    if not cart:
        return jsonify({"error": "Cart is empty"}), 400

    items = ",".join(cart.keys())
    quantities = ",".join(
        str(details["quantity"]) for details in cart.values()
    )
    prices = ",".join(
        str(
            cursor.execute(
                "SELECT price FROM items WHERE id = ?", (item_id,)
            ).fetchone()["price"]
        )
        for item_id in cart
    )

    cursor.execute(
        "INSERT INTO orders (status, user_id, items, prices, quantities, delivery_location) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "placed",
            user_id,
            items,
            prices,
            quantities,
            delivery_location,
        ),
    )

    user_cursor.execute(
        "UPDATE users SET cart = '{}' WHERE user_id = ?", (user_id,)
    )
    conn.commit()
    user_conn.commit()
    conn.close()
    user_conn.close()

    return redirect(url_for("home"))


@app.route("/deliver")
def deliver():
    response = requests.get(
        f"{SERVER_URL}/deliveries", timeout=REQUEST_TIMEOUT
    )
    deliveries = response.json()
    return render_template(
        "deliver.html", deliveries=deliveries.values()
    )


@app.route("/delivery/<delivery_id>")
def delivery_details(delivery_id):
    response = requests.get(
        f"{SERVER_URL}/delivery/{delivery_id}", timeout=REQUEST_TIMEOUT
    )
    if response.status_code == 200:
        delivery = response.json()
        return render_template(
            "delivery_details.html", delivery=delivery
        )
    return "Delivery not found", 404


@app.route("/timeline")
def delivery_timeline():
    return render_template("deliverer_timeline.html")


if __name__ == "__main__":
    app.run(port=8000, debug=get_debug_mode())
