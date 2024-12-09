"""
app.py
This file contains the main application logic for the U-Store web 
application. It handles the routing and rendering of different pages, 
as well as the interaction with the backend server and database.
"""

import json
import logging
import requests
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from auth import auth_bp, authenticate
from config import get_debug_mode, SECRET_KEY
from database import (
    get_main_db_connection,
    get_user_db_connection,
    init_user_db,
)
from db_utils import update_order_claim_status, get_user_cart
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.register_blueprint(auth_bp)

SERVER_URL = "http://localhost:5150"
REQUEST_TIMEOUT = 5
DELIVERY_FEE_PERCENTAGE = 0.1


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
        """SELECT * FROM orders
        WHERE user_id = ?
        ORDER BY timestamp DESC""",
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


def add_phone_number_column():
    """Add phone number column to users table."""
    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute("ALTER TABLE users ADD COLUMN phone_number TEXT;")
    conn.commit()
    conn.close()


@app.route("/", methods=["GET"])
@app.route("/index", methods=["GET"])
def home():
    """Display the home page and initialize user if needed."""
    username = authenticate()
    session["user_id"] = username

    conn = get_user_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """INSERT OR IGNORE INTO users (user_id, name, cart)
        VALUES (?, ?, '{}')""",
        (session["user_id"], username),
    )
    conn.commit()

    user = cursor.execute(
        "SELECT phone_number, venmo_handle FROM users WHERE user_id = ?",
        (session["user_id"],),
    ).fetchone()
    conn.close()

    if not user["phone_number"] or not user["venmo_handle"]:
        return redirect(url_for("profile"))

    return render_template("home.html", username=username)


@app.route("/shop")
def shop():
    """Display items available in the shop and current order if any."""
    username = authenticate()
    try:
        response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        sample_items = response.json()

        # Get raw database categories and convert to display format
        categories = set()
        for item in sample_items.values():
            db_category = item.get("category", "")
            pretty_category = db_category.replace("_", " ").title()
            categories.add(pretty_category)

    except (requests.RequestException, ValueError) as e:
        logging.error("Error fetching shop items: %s", str(e))
        flash("Unable to load shop items. Please try again later.")
        return redirect(url_for("home"))

    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    # Convert categories to list and sort
    categories = sorted(list(categories))

    # Check if user has favorites
    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) as count FROM favorites WHERE user_id = ?", (user_id,)
    )
    favorite_count = cursor.fetchone()["count"]

    # Add Favorites to the beginning if user has favorites
    if favorite_count > 0:
        categories.insert(0, "Favorites")
    conn.close()

    # Get current order
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM orders
        WHERE user_id = ? AND status IN ('PLACED', 'CLAIMED')
        ORDER BY timestamp DESC LIMIT 1""",
        (user_id,),
    )
    current_order = cursor.fetchone()
    conn.close()

    return render_template(
        "shop.html",
        categories=categories,  # Now ordered with Favorites first
        current_order=current_order,
        username=username,
    )


@app.route("/favorites")
def favorites_view():
    """Display user's favorite items."""
    username = authenticate()
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    # Get user's favorites
    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT item_id FROM favorites WHERE user_id = ?", (user_id,))
    favorite_items = {str(row["item_id"]) for row in cursor.fetchall()}
    conn.close()

    # Get all items
    response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
    all_items = response.json()

    # Filter for favorites only
    favorite_items_dict = {
        item_id: item
        for item_id, item in all_items.items()
        if item_id in favorite_items
    }

    return render_template(
        "category_view.html",
        category="Favorites",
        items=favorite_items_dict,
        favorites=favorite_items,
        username=username,
    )


@app.route("/get_category_items")
def get_category_items():
    """Return items for a specific category in JSON format."""
    category = request.args.get("category")
    if not category:
        return jsonify({"error": "Category not specified"}), 400

    # Handle 'Favorites' category specially
    if category == "Favorites":
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "User not logged in"}), 401

        conn = get_user_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT item_id FROM favorites WHERE user_id = ?", (user_id,))
        favorite_items = {str(row["item_id"]) for row in cursor.fetchall()}
        conn.close()

        response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
        all_items = response.json()
        items_in_category = {k: v for k, v in all_items.items() if k in favorite_items}
    else:
        # Convert pretty category name back to database format
        # Handle both single and multi-word categories
        db_category = category.upper()

        response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
        all_items = response.json()

        # Match category ignoring spaces and case
        items_in_category = {
            k: v
            for k, v in all_items.items()
            if v.get("category", "").replace(" ", "") == db_category.replace(" ", "")
        }

    # Add favorite status to all items
    user_id = session.get("user_id")
    favorite_item_ids = set()
    if user_id:
        conn = get_user_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT item_id FROM favorites WHERE user_id = ?", (user_id,))
        favorite_items = cursor.fetchall()
        conn.close()
        favorite_item_ids = {str(row["item_id"]) for row in favorite_items}

    for item_id_str, item in items_in_category.items():
        item["is_favorite"] = item_id_str in favorite_item_ids

    return jsonify({"items": items_in_category})


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
        """SELECT * FROM orders
        WHERE user_id = ?
        ORDER BY timestamp DESC LIMIT 1""",
        (user_id,),
    )
    order = cursor.fetchone()

    deliverer_venmo = None
    deliverer_phone = None
    deliverer_avg_rating = None
    if order and order["claimed_by"]:
        deliverer_avg_rating = get_average_rating(order["claimed_by"], 'deliverer')

    if order and order["claimed_by"]:
        user_conn = get_user_db_connection()
        user_cursor = user_conn.cursor()
        user_cursor.execute(
            """SELECT venmo_handle, phone_number
            FROM users WHERE user_id = ?""",
            (order["claimed_by"],),
        )
        deliverer = user_cursor.fetchone()
        if deliverer:
            deliverer_venmo = deliverer["venmo_handle"]
            deliverer_phone = deliverer["phone_number"]
        user_conn.close()

    conn.close()

    if not order:
        return "No orders found."

    order_dict = dict(order)
    order_dict["timeline"] = json.loads(order_dict.get("timeline", "{}"))
    order_dict["cart"] = json.loads(order_dict.get("cart", "{}"))

    # Convert the timestamp to EST
    if "timestamp" in order_dict and order_dict["timestamp"]:
        order_dict["timestamp"] = convert_to_est(order_dict["timestamp"])

    return render_template(
        "shopper_timeline.html",
        order=order_dict,
        deliverer_venmo=deliverer_venmo,
        deliverer_phone=deliverer_phone,
        username=username,
    )


@app.route("/category_view/<category>")
def category_view(category):
    """Display items in a specific category."""
    username = authenticate()
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user_id = session.get("user_id")

    # Get user's favorites
    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT item_id FROM favorites WHERE user_id = ?", (user_id,))
    favorites = {str(row["item_id"]) for row in cursor.fetchall()}
    conn.close()

    # Get items
    response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
    sample_items = response.json()

    # Filter items for this category - case insensitive comparison
    items_in_category = {
        k: v
        for k, v in sample_items.items()
        if v.get("category", "").upper() == category.upper()
    }

    return render_template(
        "category_view.html",
        category=category,
        items=items_in_category,
        favorites=favorites,
        username=username,
    )


@app.route("/cart_view")
def cart_view():
    """Display the cart view with item subtotals and total cost."""
    username = authenticate()
    if "user_id" not in session:
        return redirect(url_for("home"))

    items_response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
    cart_response = requests.get(
        f"{SERVER_URL}/cart",
        json={"user_id": session["user_id"]},
        timeout=REQUEST_TIMEOUT,
    )

    sample_items = items_response.json()
    cart = cart_response.json()

    subtotal = sum(
        details.get("quantity", 0) * sample_items.get(item_id, {}).get("price", 0)
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
    """Delete an item from the cart and return updated cart info."""
    user_id = session["user_id"]
    response = requests.post(
        f"{SERVER_URL}/cart",
        json={
            "user_id": user_id,
            "item_id": item_id,
            "action": "delete",
        },
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code != 200:
        return (
            jsonify({"success": False, "error": "Failed to delete item"}),
            500,
        )

    cart_response = requests.get(
        f"{SERVER_URL}/cart",
        json={"user_id": user_id},
        timeout=REQUEST_TIMEOUT,
    )
    cart = cart_response.json()

    items_response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
    items = items_response.json()

    subtotal = sum(
        details.get("quantity", 0) * items.get(item_id, {}).get("price", 0)
        for item_id, details in cart.items()
        if isinstance(details, dict)
    )
    delivery_fee = round(subtotal * DELIVERY_FEE_PERCENTAGE, 2)
    total = round(subtotal + delivery_fee, 2)

    return jsonify(
        {
            "success": True,
            "cart": cart,
            "subtotal": f"{subtotal:.2f}",
            "delivery_fee": f"{delivery_fee:.2f}",
            "total": f"{total:.2f}",
        }
    )


@app.route("/update_cart/<item_id>/<action>", methods=["POST"])
def update_cart(item_id, action):
    """Update the cart by increasing or decreasing item quantities."""
    user_id = session["user_id"]

    if action == "increase":
        response = requests.post(
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
            response = requests.post(
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
            response = requests.post(
                f"{SERVER_URL}/cart",
                json={
                    "user_id": user_id,
                    "item_id": item_id,
                    "action": "delete",
                },
                timeout=REQUEST_TIMEOUT,
            )
        else:
            return (
                jsonify({"success": False, "error": "Item not in cart"}),
                400,
            )
    else:
        return (
            jsonify({"success": False, "error": "Invalid action"}),
            400,
        )

    if response.status_code != 200:
        return (
            jsonify({"success": False, "error": "Failed to update cart"}),
            500,
        )

    return jsonify({"success": True})


@app.route("/get_cart_data")
def get_cart_data():
    """Return the current cart data and totals."""
    user_id = session.get("user_id")
    if not user_id:
        return (
            jsonify({"success": False, "error": "User not logged in"}),
            401,
        )

    cart_response = requests.get(
        f"{SERVER_URL}/cart",
        json={"user_id": user_id},
        timeout=REQUEST_TIMEOUT,
    )

    if cart_response.status_code != 200:
        return (
            jsonify({"success": False, "error": "Failed to fetch cart data"}),
            500,
        )

    items_response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
    items = items_response.json()

    cart = cart_response.json()
    subtotal = sum(
        details.get("quantity", 0) * items.get(item_id, {}).get("price", 0)
        for item_id, details in cart.items()
        if isinstance(details, dict)
    )
    delivery_fee = round(subtotal * DELIVERY_FEE_PERCENTAGE, 2)
    total = round(subtotal + delivery_fee, 2)

    return jsonify(
        {
            "success": True,
            "cart": cart,
            "items": items,
            "subtotal": f"{subtotal:.2f}",
            "delivery_fee": f"{delivery_fee:.2f}",
            "total": f"{total:.2f}",
        }
    )


@app.route("/order_status/<int:order_id>")
def order_status(order_id):
    """Return the timeline status of an order in JSON format."""
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT timeline FROM orders WHERE id = ?", (order_id,))
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


@app.route("/logout_confirmation")
def logout_confirmation():
    """Display logout confirmation page"""

    return render_template("logout_confirmation.html")


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

    items_response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
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
            "PLACED",
            user_id,
            total_items,
            json.dumps(cart),
            delivery_location,
            json.dumps(timeline),
        ),
    )

    conn.commit()
    conn.close()

    user_cursor.execute("UPDATE users SET cart = '{}' WHERE user_id = ?", (user_id,))

    user_conn.commit()
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

    available_deliveries = cursor.execute(
        "SELECT * FROM orders WHERE status = 'PLACED'"
    ).fetchall()

    my_deliveries = cursor.execute(
        """SELECT * FROM orders
        WHERE status = 'CLAIMED' AND claimed_by = ?""",
        (user_id,),
    ).fetchall()

    available_deliveries = [dict(delivery) for delivery in available_deliveries]
    my_deliveries = [dict(delivery) for delivery in my_deliveries]

    for delivery in available_deliveries + my_deliveries:
        cart = json.loads(delivery["cart"])
        subtotal = sum(item["quantity"] * item["price"] for item in cart.values())
        delivery["earnings"] = round(subtotal * DELIVERY_FEE_PERCENTAGE, 2)

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
    return redirect(url_for("deliverer_timeline", delivery_id=delivery_id))


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
                            "error": "Previous step must be completed first.",
                        }
                    ),
                    400,
                )
    else:
        if any(
            timeline.get(steps[i], False) for i in range(step_index + 1, len(steps))
        ):
            conn.close()
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Cannot uncheck step with completed next steps.",
                    }
                ),
                400,
            )

    timeline[step] = checked
    cursor.execute(
        "UPDATE orders SET timeline = ? WHERE id = ?",
        (json.dumps(timeline), order_id),
    )
    conn.commit()
    conn.close()

    return jsonify({"success": True, "timeline": timeline}), 200


@app.route("/add_favorite/<item_id>", methods=["POST"])
def add_favorite(item_id):
    """Add an item to the user's favorites."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "User not logged in"}), 401

    logging.info("Adding favorite: user_id=%s, item_id=%s", user_id, item_id)

    conn = get_user_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT OR IGNORE INTO favorites (user_id, item_id)
            VALUES (?, ?)""",
            (user_id, item_id),
        )
        conn.commit()
        return jsonify({"success": True}), 200
    except Exception as e:
        logging.error("Error adding favorite: %s", str(e))
        return jsonify({"success": False, "error": "An internal error has occurred."}), 500
    finally:
        conn.close()


@app.route("/remove_favorite/<item_id>", methods=["POST"])
def remove_favorite(item_id):
    """Remove an item from the user's favorites."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "User not logged in"}), 401

    logging.info("Removing favorite: user_id=%s, item_id=%s", user_id, item_id)

    conn = get_user_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM favorites WHERE user_id = ? AND item_id = ?",
            (user_id, item_id),
        )
        conn.commit()
        return jsonify({"success": True}), 200
    except Exception as e:
        logging.error("Error removing favorite: %s", str(e))
        return jsonify({"success": False, "error": "An internal error has occurred."}), 500
    finally:
        conn.close()


@app.route("/profile", methods=["GET", "POST"])
def profile():
    username = authenticate()
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    user_id = session["user_id"]

    # Connect to both databases
    user_conn = get_user_db_connection()
    main_conn = get_main_db_connection()

    user_cursor = user_conn.cursor()
    main_cursor = main_conn.cursor()

    user = user_cursor.execute(
        "SELECT phone_number, venmo_handle FROM users WHERE user_id = ?",
        (session["user_id"],),
    ).fetchone()

    if not user["phone_number"] or not user["venmo_handle"]:
        flash("You have not yet submitted your phone number and Venmo handle. Please complete your profile before continuing.", "warning")

    # If the user updates profile info
    if request.method == "POST":
        venmo_handle = request.form.get("venmo_handle")
        phone_number = request.form.get("phone_number")
        user_cursor.execute(
            """UPDATE users
            SET venmo_handle = ?, phone_number = ?
            WHERE user_id = ?""",
            (venmo_handle, phone_number, user_id),
        )
        user_conn.commit()
        user_conn.close()
        session.pop('_flashes')
        flash("Profile updated successfully!")
        return redirect(url_for("profile"))

    # Fetch venmo handle and phone number
    user = user_cursor.execute(
        "SELECT venmo_handle, phone_number FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    # Fetch favorite item_ids
    user_cursor.execute("SELECT item_id FROM favorites WHERE user_id = ?", (user_id,))
    favorite_item_ids = [row["item_id"] for row in user_cursor.fetchall()]

    # Fetch details for favorite items from items table
    favorite_items = []
    if favorite_item_ids:
        placeholder = ",".join("?" for _ in favorite_item_ids)  # placeholders for IN clause
        query = f"SELECT id, name, price, category FROM items WHERE id IN ({placeholder})"
        favorite_items = main_cursor.execute(query, favorite_item_ids).fetchall()

    user_conn.close()
    main_conn.close()

    # Fetch user data, orders, and stats
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

    # Get average ratings
    deliverer_avg_rating = get_average_rating(user_id, 'deliverer')
    shopper_avg_rating = get_average_rating(user_id, 'shopper')

    return render_template(
        "profile.html",
        username=username,
        orders=orders_with_totals,
        stats=stats,
        user_profile=user_profile,
        venmo_handle=user["venmo_handle"] if user else "",
        phone_number=user["phone_number"] if user else "",
        favorites=favorite_items,  # Pass favorite items to the template
        deliverer_avg_rating=deliverer_avg_rating,
        shopper_avg_rating=shopper_avg_rating
    )



@app.route("/get_cart_count", methods=["GET"])
def get_cart_count():
    """Return the current cart count for the logged-in user."""
    user_id = session.get("user_id")
    if not user_id:
        return (
            jsonify({"success": False, "error": "User not logged in"}),
            401,
        )

    response = requests.get(
        f"{SERVER_URL}/cart",
        json={"user_id": user_id},
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code != 200:
        return (
            jsonify({"success": False, "error": "Failed to fetch cart data"}),
            500,
        )

    items_in_cart = len(response.json())
    return jsonify({"success": True, "cart_count": items_in_cart})


@app.route("/get_cart_status", methods=["GET"])
def get_cart_status():
    """Return the current cart status for the logged-in user."""
    user_id = session.get("user_id")
    if not user_id:
        return (
            jsonify({"success": False, "error": "User not logged in"}),
            401,
        )

    user = get_user_cart(user_id)

    if user is None:
        return (
            jsonify({"success": False, "error": "User not found"}),
            404,
        )

    cart = json.loads(user["cart"]) if user["cart"] else {}

    return jsonify({"success": True, "cart": cart})


@app.route("/deliverer_timeline/<int:delivery_id>")
def deliverer_timeline(delivery_id):
    current_username = authenticate()
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    conn = get_main_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM orders WHERE id = ?", (delivery_id,))
    order_row = cursor.fetchone()
    conn.close()

    if not order_row:
        return "Order not found.", 404

    order = dict(order_row)

    # Now we can safely call get_average_rating on order['user_id']
    shopper_avg_rating = get_average_rating(order["user_id"], 'shopper')

    user_conn = get_user_db_connection()
    user_cursor = user_conn.cursor()
    user_cursor.execute(
        """SELECT venmo_handle, phone_number
        FROM users WHERE user_id = ?""",
        (order["user_id"],),
    )
    shopper = user_cursor.fetchone()
    if shopper:
        shopper_venmo = shopper["venmo_handle"]
        shopper_phone = shopper["phone_number"]
    else:
        shopper_venmo = None
        shopper_phone = None
    user_conn.close()

    order["timeline"] = json.loads(order.get("timeline", "{}"))
    order["cart"] = json.loads(order.get("cart", "{}"))

     # Convert the timestamp to EST
    if "timestamp" in order and order["timestamp"]:
        order["timestamp"] = convert_to_est(order["timestamp"])

    return render_template(
        "deliverer_timeline.html",
        order=order,
        shopper_venmo=shopper_venmo,
        shopper_phone=shopper_phone,
        shopper_avg_rating=shopper_avg_rating,
        username=current_username,
    )



@app.route("/order_details/<int:order_id>")
def order_details(order_id):
    """Display details of a specific order."""
    current_username = authenticate()
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    conn = get_main_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    order_row = cursor.fetchone()
    conn.commit()
    conn.close()

    if not order_row:
        return "Order not found.", 404

    order = dict(order_row)
    order["cart"] = json.loads(order.get("cart", "{}"))
    cart = order["cart"]

    subtotal = sum(
        details.get("quantity", 0) * details.get("price", 0)
        for item_id, details in cart.items()
    )

    # Convert the timestamp to EST
    if "timestamp" in order and order["timestamp"]:
        order["timestamp"] = convert_to_est(order["timestamp"])

    return render_template(
        "order_details.html",
        order=order,
        subtotal=subtotal,
        username=current_username,
    )

@app.route("/submit_rating", methods=["POST"])
def submit_rating():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json()
    rated_user_id = data.get("rated_user_id")
    rater_role = data.get("rater_role")  # 'shopper' or 'deliverer'
    rating = data.get("rating")

    if not rated_user_id or not rater_role or not rating:
        return jsonify({"success": False, "error": "Missing fields"}), 400

    # Validate that the order is delivered and that the user is involved in that order:
    order_id = data.get("order_id")
    if not order_id:
        return jsonify({"success": False, "error": "Order ID required"}), 400

    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()
    conn.close()

    if not order:
        return jsonify({"success": False, "error": "Order not found"}), 404

    # Check if order is delivered
    timeline = json.loads(order["timeline"])
    if not timeline.get("Delivered"):
        return jsonify({"success": False, "error": "Cannot rate before order is delivered"}), 400

    # Check if the user rating is either the shopper or the deliverer involved
    if rater_role == 'deliverer':
        # deliverer is rating the shopper
        # deliverer must be order['claimed_by']
        if order["claimed_by"] != user_id:
            return jsonify({"success": False, "error": "You are not authorized to rate this shopper"}), 403
    elif rater_role == 'shopper':
        # shopper is rating the deliverer
        # shopper must be order['user_id']
        if order["user_id"] != user_id:
            return jsonify({"success": False, "error": "You are not authorized to rate this deliverer"}), 403
    else:
        return jsonify({"success": False, "error": "Invalid role"}), 400

    # Submit the rating
    if update_rating(rated_user_id, rater_role, int(rating)):
        return jsonify({"success": True, "redirect_url": url_for("home")}), 200
    else:
        return jsonify({"success": False, "error": "Rating update failed"}), 500


def update_rating(user_id, rater_role, rating):
    """
    user_id: the user receiving the rating
    rater_role: 'shopper' or 'deliverer' indicating which rating we update
    rating: integer rating from 1-5
    """
    if rating < 1 or rating > 5:
        return False

    conn = get_user_db_connection()
    cursor = conn.cursor()

    if rater_role == 'deliverer':
        # update shopper_rating_sum/shopper_rating_count since deliverer is being rated by a shopper
        cursor.execute("""
            UPDATE users
            SET deliverer_rating_sum = deliverer_rating_sum + ?,
                deliverer_rating_count = deliverer_rating_count + 1
            WHERE user_id = ?
        """, (rating, user_id))
    elif rater_role == 'shopper':
        # update deliverer_rating_sum/deliverer_rating_count since shopper is being rated by a deliverer
        cursor.execute("""
            UPDATE users
            SET shopper_rating_sum = shopper_rating_sum + ?,
                shopper_rating_count = shopper_rating_count + 1
            WHERE user_id = ?
        """, (rating, user_id))
    else:
        conn.close()
        return False

    conn.commit()
    conn.close()
    return True

def get_average_rating(user_id, role):
    """
    role: 'deliverer' or 'shopper'
    Returns the average rating for the specified role.
    """
    conn = get_user_db_connection()
    cursor = conn.cursor()
    if role == 'deliverer':
        cursor.execute("""
            SELECT deliverer_rating_sum AS s, deliverer_rating_count AS c
            FROM users WHERE user_id = ?
        """, (user_id,))
    else:
        cursor.execute("""
            SELECT shopper_rating_sum AS s, shopper_rating_count AS c
            FROM users WHERE user_id = ?
        """, (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row["c"] and row["c"] > 0:
        return round(row["s"] / row["c"], 1)
    return None

# Define EST as UTC-5, without DST changes
EST = timezone(timedelta(hours=-5))

def convert_to_est(timestamp_str):
    dt_utc = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
    dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    dt_est = dt_utc.astimezone(EST)
    return dt_est.strftime("%Y-%m-%d %H:%M EST")


if __name__ == "__main__":
    init_user_db()
    app.run(host="localhost", port=8000, debug=get_debug_mode())
