#!/usr/bin/env python
"""
app.py
Main application logic for the U-Store web application using PostgreSQL.
Loads credentials from secrets.txt via database.py and uses psycopg2.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
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

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.register_blueprint(auth_bp)

SERVER_URL = "http://localhost:5150"
REQUEST_TIMEOUT = 5
DELIVERY_FEE_PERCENTAGE = 0.1

est_zone = timezone(timedelta(hours=-5))


def get_user_data(user_id):
    """
    Retrieve user data from the database.

    :param user_id: The ID of the user.
    :return: A dictionary with user data.
    """
    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user


def get_user_orders(user_id):
    """
    Retrieve a list of orders associated with a user.

    :param user_id: The user's ID.
    :return: A list of orders.
    """
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM orders WHERE user_id = %s ORDER BY timestamp DESC",
        (user_id,),
    )
    orders = cursor.fetchall()
    conn.close()
    return orders


def calculate_user_stats(orders):
    """
    Calculate user stats (total orders, total items, total spent) from their orders.

    :param orders: A list of orders for the user.
    :return: A dictionary with total_orders, total_spent, total_items.
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


def get_average_rating(user_id, role):
    """
    Retrieve the average rating for a user as either a deliverer or a shopper.

    :param user_id: The ID of the user.
    :param role: "deliverer" or "shopper".
    :return: The average rating as a float or None if no ratings.
    """
    conn = get_user_db_connection()
    cursor = conn.cursor()
    if role == "deliverer":
        cursor.execute(
            """
            SELECT deliverer_rating_sum AS s, deliverer_rating_count AS c
            FROM users WHERE user_id = %s
            """,
            (user_id,),
        )
    else:
        cursor.execute(
            """
            SELECT shopper_rating_sum AS s, shopper_rating_count AS c
            FROM users WHERE user_id = %s
            """,
            (user_id,),
        )
    row = cursor.fetchone()
    conn.close()
    if row and row["c"] and row["c"] > 0:
        return round(row["s"] / row["c"], 1)
    return None


def update_rating(user_id, rater_role, rating):
    """
    Update the rating for a user based on the role of the rater.

    :param user_id: The user being rated.
    :param rater_role: 'deliverer' or 'shopper', indicating the role of the one being rated.
    :param rating: The rating value (1-5).
    :return: True if updated successfully, False otherwise.
    """
    if rating < 1 or rating > 5:
        return False
    conn = get_user_db_connection()
    cursor = conn.cursor()

    if rater_role == "deliverer":
        # deliverer being rated by shopper
        cursor.execute(
            """
            UPDATE users
            SET deliverer_rating_sum = deliverer_rating_sum + %s,
                deliverer_rating_count = deliverer_rating_count + 1
            WHERE user_id = %s
            """,
            (rating, user_id),
        )
    elif rater_role == "shopper":
        # shopper being rated by deliverer
        cursor.execute(
            """
            UPDATE users
            SET shopper_rating_sum = shopper_rating_sum + %s,
                shopper_rating_count = shopper_rating_count + 1
            WHERE user_id = %s
            """,
            (rating, user_id),
        )
    else:
        conn.close()
        return False

    conn.commit()
    conn.close()
    return True


def convert_to_est(dt_utc):
    """
    Convert a UTC datetime object to Eastern Standard Time (EST) string.

    :param dt_utc: A datetime object in UTC (aware or naive).
    :return: A formatted string in EST.
    """
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    dt_est = dt_utc.astimezone(est_zone)
    return dt_est.strftime("%Y-%m-%d %H:%M EST")


def get_delivery_stats(user_id):
    """
    Calculate delivery stats (deliveries_completed, money_made) for a deliverer.

    :param user_id: The deliverer's user ID.
    :return: A dictionary with deliveries_completed and money_made.
    """
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT cart FROM orders WHERE claimed_by = %s AND status = 'FULFILLED'",
        (user_id,),
    )
    completed_orders = cursor.fetchall()
    conn.close()

    deliveries_completed = len(completed_orders)
    money_made = 0.0
    for order in completed_orders:
        cart = json.loads(order["cart"])
        subtotal = sum(
            item.get("quantity", 0) * item.get("price", 0)
            for item in cart.values()
        )
        earnings = round(subtotal * DELIVERY_FEE_PERCENTAGE, 2)
        money_made += earnings

    return {
        "deliveries_completed": deliveries_completed,
        "money_made": round(money_made, 2),
    }


@app.route("/", methods=["GET"])
@app.route("/index", methods=["GET"])
def home():
    """
    Home page route. Authenticates the user and ensures that profile info is set.
    Redirects to profile if phone_number or venmo_handle is missing.
    """
    username = authenticate()
    session["user_id"] = username

    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO users (user_id, name, cart)
        VALUES (%s, %s, '{}')
        ON CONFLICT (user_id) DO NOTHING""",
        (session["user_id"], username),
    )
    conn.commit()

    cursor.execute(
        "SELECT phone_number, venmo_handle FROM users WHERE user_id = %s",
        (session["user_id"],),
    )
    user = cursor.fetchone()
    conn.close()

    if not user["phone_number"] or not user["venmo_handle"]:
        return redirect(url_for("profile"))

    return render_template("home.html", username=username)


@app.route("/shop")
def shop():
    """
    Shop route: fetches items, categories, and favorite count for the current user.
    Displays categories and current order status.
    """
    username = authenticate()
    try:
        response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        sample_items = response.json()

        categories = set()
        for item in sample_items.values():
            db_category = item.get("category", "")
            pretty_category = db_category.replace("_", " ").title()
            categories.add(pretty_category)
    except requests.RequestException as err:
        logging.error("Error fetching shop items: %s", str(err))
        flash("Unable to load shop items. Please try again later.")
        return redirect(url_for("home"))

    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    categories = sorted(list(categories))

    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) as count FROM favorites WHERE user_id = %s",
        (user_id,),
    )
    favorite_count = cursor.fetchone()["count"]
    conn.close()

    if favorite_count > 0:
        categories.insert(0, "Favorites")

    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM orders
        WHERE user_id = %s AND status IN ('PLACED', 'CLAIMED')
        ORDER BY timestamp DESC LIMIT 1""",
        (user_id,),
    )
    current_order = cursor.fetchone()
    conn.close()

    return render_template(
        "shop.html",
        categories=categories,
        current_order=current_order,
        username=username,
    )


@app.route("/favorites")
def favorites_view():
    """
    Favorites route: displays the user's favorite items.
    """
    username = authenticate()
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT item_id FROM favorites WHERE user_id = %s", (user_id,)
    )
    favorite_items = {str(row["item_id"]) for row in cursor.fetchall()}
    conn.close()

    response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
    all_items = response.json()

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
    """
    Fetch and return items for a given category as JSON.
    If category is "Favorites", return the user's favorites.
    """
    category = request.args.get("category")
    if not category:
        return jsonify({"error": "Category not specified"}), 400

    response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
    all_items = response.json()

    if category == "Favorites":
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "User not logged in"}), 401

        conn = get_user_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT item_id FROM favorites WHERE user_id = %s",
            (user_id,),
        )
        favorite_items = {str(row["item_id"]) for row in cursor.fetchall()}
        conn.close()

        items_in_category = {
            k: v for k, v in all_items.items() if k in favorite_items
        }
    else:
        db_category = category.upper().replace(" ", "")
        items_in_category = {
            k: v
            for k, v in all_items.items()
            if v.get("category", "").replace(" ", "").upper() == db_category
        }

    user_id = session.get("user_id")
    favorite_item_ids = set()
    if user_id:
        conn = get_user_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT item_id FROM favorites WHERE user_id = %s",
            (user_id,),
        )
        favorite_list = cursor.fetchall()
        conn.close()
        favorite_item_ids = {str(row["item_id"]) for row in favorite_list}

    for item_id_str, item in items_in_category.items():
        item["is_favorite"] = item_id_str in favorite_item_ids

    return jsonify({"items": items_in_category})


@app.route("/shopper_timeline")
def shopper_timeline():
    """
    Displays the shopper's timeline for their most recent order.
    """
    username = authenticate()
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("home"))

    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM orders
        WHERE user_id = %s
        ORDER BY timestamp DESC LIMIT 1""",
        (user_id,),
    )
    order = cursor.fetchone()

    deliverer_venmo = None
    deliverer_phone = None
    deliverer_avg_rating = None

    if order and order["claimed_by"]:
        deliverer_avg_rating = get_average_rating(order["claimed_by"], "deliverer")

    if order and order["claimed_by"]:
        user_conn = get_user_db_connection()
        user_cursor = user_conn.cursor()
        user_cursor.execute(
            "SELECT venmo_handle, phone_number FROM users WHERE user_id = %s",
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

    if "timestamp" in order_dict and order_dict["timestamp"]:
        order_dict["timestamp"] = convert_to_est(order_dict["timestamp"])

    return render_template(
        "shopper_timeline.html",
        order=order_dict,
        deliverer_venmo=deliverer_venmo,
        deliverer_phone=deliverer_phone,
        username=username,
        deliverer_avg_rating=deliverer_avg_rating,
    )


@app.route("/category_view/<category>")
def category_view(category):
    """
    Display items of a given category.
    """
    username = authenticate()
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    user_id = session["user_id"]

    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT item_id FROM favorites WHERE user_id = %s", (user_id,)
    )
    favorites = {str(row["item_id"]) for row in cursor.fetchall()}
    conn.close()

    response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
    sample_items = response.json()

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
    """
    Display the user's current cart.
    """
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
    """
    Add an item to the user's cart.
    """
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
    """
    Delete an item from the user's cart.
    """
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
        details.get("quantity", 0) * items.get(iid, {}).get("price", 0)
        for iid, details in cart.items()
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
    """
    Update the quantity of an item in the cart.
    Increase or decrease the quantity depending on action.
    """
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
        cart_data = cart_response.json()
        quantity = cart_data.get(item_id, {}).get("quantity", 0)

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
            return jsonify({"success": False, "error": "Item not in cart"}), 400
    else:
        return jsonify({"success": False, "error": "Invalid action"}), 400

    if response.status_code != 200:
        return jsonify({"success": False, "error": "Failed to update cart"}), 500

    return jsonify({"success": True})


@app.route("/get_cart_data")
def get_cart_data():
    """
    Fetch updated cart data, including totals.
    """
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "User not logged in"}), 401

    cart_response = requests.get(
        f"{SERVER_URL}/cart",
        json={"user_id": user_id},
        timeout=REQUEST_TIMEOUT,
    )

    if cart_response.status_code != 200:
        return jsonify({"success": False, "error": "Failed to fetch cart data"}), 500

    items_response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
    items = items_response.json()

    cart_data = cart_response.json()
    subtotal = sum(
        details.get("quantity", 0) * items.get(iid, {}).get("price", 0)
        for iid, details in cart_data.items()
        if isinstance(details, dict)
    )
    delivery_fee = round(subtotal * DELIVERY_FEE_PERCENTAGE, 2)
    total = round(subtotal + delivery_fee, 2)

    return jsonify(
        {
            "success": True,
            "cart": cart_data,
            "items": items,
            "subtotal": f"{subtotal:.2f}",
            "delivery_fee": f"{delivery_fee:.2f}",
            "total": f"{total:.2f}",
        }
    )


@app.route("/order_status/<int:order_id>")
def order_status(order_id):
    """
    Return the timeline for a specific order as JSON.
    """
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT timeline FROM orders WHERE id = %s", (order_id,))
    order = cursor.fetchone()
    conn.close()

    if not order:
        return jsonify({"error": "Order not found."}), 404

    timeline = json.loads(order["timeline"])
    return jsonify({"timeline": timeline})


@app.route("/order_confirmation")
def order_confirmation():
    """
    Display order confirmation page.
    """
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
    """
    Confirmation page for logout.
    """
    return render_template("logout_confirmation.html")


@app.route("/place_order", methods=["POST"])
def place_order():
    """
    Place an order with the current cart contents and specified delivery location.
    """
    user_id = session.get("user_id")
    data = request.get_json()
    delivery_location = data.get("delivery_location")

    if not delivery_location:
        return jsonify({"error": "Delivery location is required"}), 400

    user_conn = get_user_db_connection()
    user_cursor = user_conn.cursor()
    user_cursor.execute(
        "SELECT cart FROM users WHERE user_id = %s", (user_id,)
    )
    user = user_cursor.fetchone()
    cart = json.loads(user["cart"]) if user and user["cart"] else {}

    if not cart:
        return jsonify({"error": "Cart is empty"}), 400

    items_response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
    items = items_response.json()

    for item_id, details in cart.items():
        item = items.get(item_id)
        if item:
            details["price"] = item["price"]
            details["name"] = item["name"]

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
        VALUES (%s, %s, %s, %s, %s, %s)""",
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

    user_cursor.execute(
        "UPDATE users SET cart = '{}' WHERE user_id = %s", (user_id,)
    )
    user_conn.commit()
    user_conn.close()

    return jsonify({"success": True}), 200


@app.route("/deliver")
def deliver():
    """
    Display available deliveries and claimed deliveries for the current user.
    """
    current_username = authenticate()
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("home"))

    conn = get_main_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM orders WHERE status = 'PLACED'")
    available_deliveries = cursor.fetchall()

    cursor.execute(
        """SELECT * FROM orders
        WHERE status = 'CLAIMED' AND claimed_by = %s""",
        (user_id,),
    )
    my_deliveries = cursor.fetchall()

    available_deliveries = [dict(d) for d in available_deliveries]
    my_deliveries = [dict(d) for d in my_deliveries]

    for delivery in available_deliveries + my_deliveries:
        cart = json.loads(delivery["cart"])
        subtotal = sum(
            item["quantity"] * item.get("price", 0)
            for item in cart.values()
        )
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
    """
    Display details of a specific delivery (order).
    """
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
    """
    Accept a delivery (claim it).
    """
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    update_order_claim_status(user_id, delivery_id)
    return redirect(url_for("deliverer_timeline", delivery_id=delivery_id))


@app.route(
    "/decline_delivery/<delivery_id>",
    methods=["POST"],
    endpoint="decline_delivery",
)
def decline_delivery_route(delivery_id):
    """
    Decline a delivery.
    """
    response = requests.post(
        f"{SERVER_URL}/decline_delivery/{delivery_id}",
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code == 200:
        return redirect(url_for("deliver"))
    return "Error declining delivery", response.status_code


@app.route("/update_checklist", methods=["POST"])
def update_checklist():
    """
    Update the timeline checklist of a delivery (for deliverer).
    """
    data = request.get_json()
    order_id = data.get("order_id")
    step = data.get("step")
    checked = data.get("checked")

    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "User not logged in"}), 401

    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT timeline, claimed_by FROM orders WHERE id = %s",
        (order_id,),
    )
    order = cursor.fetchone()

    if not order:
        conn.close()
        return jsonify({"success": False, "error": "Order not found"}), 404

    if order["claimed_by"] != user_id:
        conn.close()
        return jsonify({"success": False, "error": "Not authorized"}), 403

    timeline = json.loads(order["timeline"])
    steps_list = [
        "Order Accepted",
        "Venmo Payment Received",
        "Shopping in U-Store",
        "Checked Out",
        "On Delivery",
        "Delivered",
    ]
    step_index = steps_list.index(step)

    if checked:
        if step_index > 0:
            previous_step = steps_list[step_index - 1]
            if not timeline.get(previous_step, False):
                conn.close()
                return (
                    jsonify(
                        {"success": False,
                         "error": "Previous step must be completed first."}
                    ),
                    400,
                )
    else:
        if any(
            timeline.get(steps_list[i], False)
            for i in range(step_index + 1, len(steps_list))
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
        "UPDATE orders SET timeline = %s WHERE id = %s",
        (json.dumps(timeline), order_id),
    )
    conn.commit()
    conn.close()

    return jsonify({"success": True, "timeline": timeline}), 200


@app.route("/add_favorite/<item_id>", methods=["POST"])
def add_favorite(item_id):
    """
    Add an item to the user's favorites.
    """
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "User not logged in"}), 401

    logging.info("Adding favorite: user_id=%s, item_id=%s", user_id, item_id)

    conn = get_user_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO favorites (user_id, item_id)
            VALUES (%s, %s)
            ON CONFLICT (user_id, item_id) DO NOTHING""",
            (user_id, item_id),
        )
        conn.commit()
        return jsonify({"success": True}), 200
    except Exception as err:  # Ideally catch specific DB exceptions
        logging.error("Error adding favorite: %s", str(err))
        return jsonify({"success": False, "error": "Internal error."}), 500
    finally:
        conn.close()


@app.route("/remove_favorite/<item_id>", methods=["POST"])
def remove_favorite(item_id):
    """
    Remove an item from the user's favorites.
    """
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "User not logged in"}), 401

    logging.info("Removing favorite: user_id=%s, item_id=%s", user_id, item_id)

    conn = get_user_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM favorites WHERE user_id = %s AND item_id = %s",
            (user_id, item_id),
        )
        conn.commit()
        return jsonify({"success": True}), 200
    except Exception as err:  # Ideally catch specific DB exceptions
        logging.error("Error removing favorite: %s", str(err))
        return jsonify({"success": False, "error": "Internal error."}), 500
    finally:
        conn.close()


@app.route("/profile", methods=["GET", "POST"])
def profile():
    """
    Profile page: allows the user to update phone number and venmo handle,
    and view their stats, favorites, and order history.
    """
    username = authenticate()
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    user_id = session["user_id"]

    user_conn = get_user_db_connection()
    main_conn = get_main_db_connection()

    user_cursor = user_conn.cursor()
    main_cursor = main_conn.cursor()

    user_cursor.execute(
        "SELECT phone_number, venmo_handle FROM users WHERE user_id = %s",
        (user_id,),
    )
    user = user_cursor.fetchone()

    if not user["phone_number"] or not user["venmo_handle"]:
        flash(
            "You have not yet submitted your phone number and Venmo handle."
            " Please complete your profile before continuing.",
            "warning",
        )

    if request.method == "POST":
        venmo_handle = request.form.get("venmo_handle")
        phone_number = request.form.get("phone_number")
        user_cursor.execute(
            """UPDATE users
            SET venmo_handle = %s, phone_number = %s
            WHERE user_id = %s""",
            (venmo_handle, phone_number, user_id),
        )
        user_conn.commit()
        user_conn.close()
        session.pop("_flashes", None)
        flash("Profile updated successfully!")
        return redirect(url_for("profile"))

    user_cursor.execute(
        "SELECT venmo_handle, phone_number FROM users WHERE user_id = %s",
        (user_id,),
    )
    user = user_cursor.fetchone()

    user_cursor.execute(
        "SELECT item_id FROM favorites WHERE user_id = %s", (user_id,)
    )
    favorite_item_ids = [row["item_id"] for row in user_cursor.fetchall()]

    favorite_items = []
    if favorite_item_ids:
        placeholder = ",".join(["%s"] * len(favorite_item_ids))
        query = (
            f"SELECT store_code as id, name, price, category FROM items "
            f"WHERE store_code IN ({placeholder})"
        )
        main_cursor.execute(query, favorite_item_ids)
        favorite_items = main_cursor.fetchall()

    user_conn.close()
    main_conn.close()

    user_profile = get_user_data(user_id)
    orders = get_user_orders(user_id)
    stats = calculate_user_stats(orders)

    orders_with_totals = []
    for order in orders:
        cart = json.loads(order["cart"])
        subtotal = sum(
            details.get("quantity", 0) * details.get("price", 0)
            for details in cart.values()
        )
        order_data = dict(order)
        order_data["total"] = round(subtotal, 2)
        orders_with_totals.append(order_data)

    deliverer_avg_rating = get_average_rating(user_id, "deliverer")
    shopper_avg_rating = get_average_rating(user_id, "shopper")

    delivery_stats = get_delivery_stats(user_id)

    return render_template(
        "profile.html",
        username=username,
        orders=orders_with_totals,
        stats=stats,
        user_profile=user_profile,
        venmo_handle=user["venmo_handle"] if user else "",
        phone_number=user["phone_number"] if user else "",
        favorites=favorite_items,
        deliverer_avg_rating=deliverer_avg_rating,
        shopper_avg_rating=shopper_avg_rating,
        delivery_stats=delivery_stats,
    )


@app.route("/get_cart_count", methods=["GET"])
def get_cart_count():
    """
    Get the number of items in the user's cart.
    """
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "User not logged in"}), 401

    response = requests.get(
        f"{SERVER_URL}/cart",
        json={"user_id": user_id},
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code != 200:
        return jsonify({"success": False, "error": "Failed to fetch cart data"}), 500

    items_in_cart = len(response.json())
    return jsonify({"success": True, "cart_count": items_in_cart})


@app.route("/get_cart_status", methods=["GET"])
def get_cart_status():
    """
    Fetch the current cart status for the user.
    """
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "User not logged in"}), 401

    user = get_user_cart(user_id)
    if user is None:
        return jsonify({"success": False, "error": "User not found"}), 404

    cart = json.loads(user["cart"]) if user["cart"] else {}
    return jsonify({"success": True, "cart": cart})


@app.route("/deliverer_timeline/<int:delivery_id>")
def deliverer_timeline(delivery_id):
    """
    Display the deliverer's timeline for a given delivery.
    """
    current_username = authenticate()
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = %s", (delivery_id,))
    order_row = cursor.fetchone()
    conn.close()

    if not order_row:
        return "Order not found.", 404

    order = dict(order_row)
    shopper_avg_rating = get_average_rating(order["user_id"], "shopper")

    user_conn = get_user_db_connection()
    user_cursor = user_conn.cursor()
    user_cursor.execute(
        "SELECT venmo_handle, phone_number FROM users WHERE user_id = %s",
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
    """
    Display details of a specific order for the user.
    """
    current_username = authenticate()
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
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
        for details in cart.values()
    )

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
    """
    Submit a rating for a user (deliverer or shopper) after order completion.
    """
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json()
    rated_user_id = data.get("rated_user_id")
    rater_role = data.get("rater_role")
    rating = data.get("rating")
    order_id = data.get("order_id")

    if not (rated_user_id and rater_role and rating and order_id):
        return jsonify({"success": False, "error": "Missing fields"}), 400

    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
    order = cursor.fetchone()

    if not order:
        conn.close()
        return jsonify({"success": False, "error": "Order not found"}), 404

    timeline = json.loads(order["timeline"])
    if not timeline.get("Delivered"):
        conn.close()
        return jsonify(
            {"success": False, "error": "Cannot rate before order is delivered"}
        ), 400

    # Authorization check
    if rater_role == "deliverer":
        if order["claimed_by"] != user_id:
            conn.close()
            return jsonify({"success": False, "error": "Not authorized"}), 403
    elif rater_role == "shopper":
        if order["user_id"] != user_id:
            conn.close()
            return jsonify({"success": False, "error": "Not authorized"}), 403
    else:
        conn.close()
        return jsonify({"success": False, "error": "Invalid role"}), 400

    if not update_rating(rated_user_id, rater_role, int(rating)):
        conn.close()
        return jsonify({"success": False, "error": "Rating update failed"}), 500

    # Mark that this user role has rated
    if rater_role == "deliverer":
        cursor.execute(
            "UPDATE orders SET deliverer_rated = TRUE WHERE id = %s",
            (order_id,),
        )
    else:
        cursor.execute(
            "UPDATE orders SET shopper_rated = TRUE WHERE id = %s",
            (order_id,),
        )

    cursor.execute(
        "SELECT shopper_rated, deliverer_rated FROM orders WHERE id = %s",
        (order_id,),
    )
    row = cursor.fetchone()
    if row["shopper_rated"] and row["deliverer_rated"]:
        # Mark order as FULFILLED
        cursor.execute(
            "UPDATE orders SET status = 'FULFILLED' WHERE id = %s",
            (order_id,),
        )

    conn.commit()
    conn.close()

    return jsonify({"success": True, "redirect_url": url_for("home")}), 200


if __name__ == "__main__":
    init_user_db()
    app.run(host="localhost", port=8000, debug=get_debug_mode())
