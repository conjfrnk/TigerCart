#!/usr/bin/env python
"""
app.py
Main application logic for the U-Store web application using PostgreSQL.
Loads credentials from secrets.txt via database.py and uses psycopg2.

This version includes code to compute real delivery stats and provide `delivery_stats` to the profile template.
"""

import json
import logging
import requests
from datetime import datetime, timezone, timedelta
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
    current_app
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

EST = timezone(timedelta(hours=-5))

# –––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––
# User Data Management
# –––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––


# Function to get user data from the user database
def get_user_data(user_id):
    conn = get_user_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

# Function to get user orders from the main database
def get_user_orders(user_id):
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM orders WHERE user_id = %s ORDER BY timestamp DESC",
        (user_id,),
    )
    orders = cursor.fetchall()
    conn.close()
    return orders

# Function to calculate user stats
def calculate_user_stats(orders):
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

# Function to get the average rating of a user for a given role
def get_average_rating(user_id, role):
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

# Function to update the rating of a user
def update_rating(user_id, rater_role, rating):
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

# Function to get the delivery stats for a deliverer
def get_delivery_stats(user_id):
    """
    Calculate real delivery stats for a deliverer.
    Consider a delivery completed if status='FULFILLED' and claimed_by=user_id.
    We'll sum up all earnings from these deliveries.
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


# –––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––
# Routes for Navigation and Rendering
# –––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––

# Route for the home page
@app.route("/", methods=["GET"])
@app.route("/index", methods=["GET"])
def home():
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

# Route for the shop page
@app.route("/shop")
def shop():
    username = authenticate()
    try:
        response = requests.get(
            f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        sample_items = response.json()

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

# Route for the favorites page
@app.route("/favorites")
def favorites_view():
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

    response = requests.get(
        f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT
    )
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

# Route for the shopper timeline page
@app.route("/shopper_timeline")
def shopper_timeline():
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
        deliverer_avg_rating = get_average_rating(
            order["claimed_by"], "deliverer"
        )

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
    order_dict["timeline"] = json.loads(
        order_dict.get("timeline", "{}")
    )
    order_dict["cart"] = json.loads(order_dict.get("cart", "{}"))

    if "timestamp" in order_dict and order_dict["timestamp"]:
        order_dict["timestamp"] = convert_to_est(
            order_dict["timestamp"]
        )

    return render_template(
        "shopper_timeline.html",
        order=order_dict,
        deliverer_venmo=deliverer_venmo,
        deliverer_phone=deliverer_phone,
        username=username,
    )

# Route for the category view page
@app.route("/category_view/<category>")
def category_view(category):
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

    response = requests.get(
        f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT
    )
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

# Route for the cart view page
@app.route("/cart_view")
def cart_view():
    username = authenticate()
    user_id = session.get("user_id")
    if "user_id" not in session:
        return redirect(url_for("home"))

    try:
        # Fetch items and cart data
        items_response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
        cart_response = requests.get(
            f"{SERVER_URL}/cart",
            params={"user_id": user_id},
            timeout=REQUEST_TIMEOUT,
        )

        # Parse responses
        sample_items = items_response.json()

        # Check if cart response is valid
        if cart_response.status_code != 200:
            logging.error(f"Cart fetch failed: {cart_response.json()}")
            cart = {}
        else:
            cart = cart_response.json()

    except Exception as e:
        logging.error(f"Error fetching cart data: {e}")
        sample_items = {}
        cart = {}

    # Calculate subtotal, delivery fee, and total
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

# Function to return the deliverer page (deliver.html)
@app.route("/deliver")
def deliver():
    current_username = authenticate()
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("home"))

    conn = get_main_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM orders WHERE status = 'PLACED' AND user_id != %s", (user_id,))
    available_deliveries = cursor.fetchall()

    cursor.execute(
        """SELECT * FROM orders
        WHERE status = 'CLAIMED' AND claimed_by = %s""",
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
            item["quantity"] * item.get("price", 0)
            for item in cart.values()
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

# Function to see the details of a delivery
@app.route("/delivery/<delivery_id>")
def delivery_details(delivery_id):
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

# Function to view the profile page
@app.route("/profile", methods=["GET", "POST"])
def profile():
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
            "You have not yet submitted your phone number and Venmo handle. Please complete your profile before continuing.",
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
    favorite_item_ids = [
        row["item_id"] for row in user_cursor.fetchall()
    ]

    favorite_items = []
    if favorite_item_ids:
        placeholder = ",".join(["%s"] * len(favorite_item_ids))
        query = f"SELECT store_code as id, name, price, category FROM items WHERE store_code IN ({placeholder})"
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

    # Compute delivery stats for the user as a deliverer
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
        delivery_stats=delivery_stats,  # now includes money_made
    )

# Function to confirm an order
@app.route("/order_confirmation")
def order_confirmation():
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

# Route to confirm a logout
@app.route("/logout_confirmation")
def logout_confirmation():
    return render_template("logout_confirmation.html")


# –––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––
# API Endpoints for Data Operations
# –––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––

# Function to get the items in a category
@app.route("/get_category_items")
def get_category_items():
    category = request.args.get("category")
    if not category:
        return jsonify({"error": "Category not specified"}), 400

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
        favorite_items = {
            str(row["item_id"]) for row in cursor.fetchall()
        }
        conn.close()

        response = requests.get(
            f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT
        )
        all_items = response.json()
        items_in_category = {
            k: v for k, v in all_items.items() if k in favorite_items
        }
    else:
        db_category = category.upper()
        response = requests.get(
            f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT
        )
        all_items = response.json()
        items_in_category = {
            k: v
            for k, v in all_items.items()
            if v.get("category", "").replace(" ", "")
            == db_category.replace(" ", "")
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
        favorite_items = cursor.fetchall()
        conn.close()
        favorite_item_ids = {
            str(row["item_id"]) for row in favorite_items
        }

    for item_id_str, item in items_in_category.items():
        item["is_favorite"] = item_id_str in favorite_item_ids

    return jsonify({"items": items_in_category})

# Function to get the items in the cart
@app.route("/get_cart_data")
def get_cart_data():
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
            jsonify(
                {"success": False, "error": "Failed to fetch cart data"}
            ),
            500,
        )

    items_response = requests.get(
        f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT
    )
    items = items_response.json()

    cart = cart_response.json()
    subtotal = sum(
        details.get("quantity", 0)
        * items.get(item_id, {}).get("price", 0)
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

# Function to get the number of items in the cart
@app.route("/get_cart_count", methods=["GET"])
def get_cart_count():
    user_id = session.get("user_id")
    if not user_id:
        return (
            jsonify({"success": False, "error": "User not logged in"}),
            401,
        )

    response = requests.get(
        f"{SERVER_URL}/cart",
        params={"user_id": user_id},
        timeout=REQUEST_TIMEOUT,
    )


    if response.status_code != 200:
        return (
            jsonify(
                {"success": False, "error": "Failed to fetch cart data"}
            ),
            500,
        )

    items_in_cart = len(response.json())
    return jsonify({"success": True, "cart_count": items_in_cart})

# Function to get the cart status
@app.route("/get_cart_status", methods=["GET"])
def get_cart_status():
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


# –––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––
# Shopping and Cart Management
# –––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––

# Function to add an item to the cart
@app.route("/add_to_cart/<item_id>", methods=["POST"])
def add_to_cart(item_id):
    user_id = session.get("user_id")

    if not user_id:
        return jsonify({"error": "User not logged in"}), 401

    headers = {
        "Content-Type": "application/json",
    }

    # Send the POST request to the API server
    payload = {
        "user_id": user_id,
        "item_id": item_id,
        "action": "add"
    }

    try:
        response = requests.post(
            f"{SERVER_URL}/cart", headers=headers, json=payload, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()  # Raise exception for 4xx/5xx responses

        # Attempt to parse JSON response
        return jsonify(response.json())

    except requests.exceptions.HTTPError as http_err:
        # Response is an error; try to parse JSON, if fails, return a default error
        try:
            error_data = response.json()
        except ValueError:
            # The response isn't JSON; just return a generic error
            error_data = {"error": "Failed to add item"}

        current_app.logger.error(f"HTTP error occurred: {http_err} - {response.text}")
        return jsonify(error_data), response.status_code

    except Exception as e:
        current_app.logger.error(f"Error adding item to cart: {e}")
        return jsonify({"error": "Internal server error"}), 500





# Function to delete an item from the cart
@app.route("/delete_item/<item_id>", methods=["POST"])
def delete_item(item_id):
    user_id = session["user_id"]
    response = requests.post(
        f"{SERVER_URL}/cart",
        json={"user_id": user_id, "item_id": item_id, "action": "delete"},
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code != 200:
        return jsonify({"success": False, "error": "Failed to delete item"}), 500

    cart_response = requests.get(
        f"{SERVER_URL}/cart",
        params={"user_id": user_id},
        timeout=REQUEST_TIMEOUT,
    )

    if cart_response.status_code != 200:
        return jsonify({"success": False, "error": "Failed to fetch updated cart"}), 500

    cart = cart_response.json()  # Parse the cart here

    items_response = requests.get(
        f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT
    )
    items = items_response.json()

    subtotal = sum(
        details.get("quantity", 0)
        * items.get(i_id, {}).get("price", 0)
        for i_id, details in cart.items()
        if isinstance(details, dict)
    )
    delivery_fee = round(subtotal * DELIVERY_FEE_PERCENTAGE, 2)
    total = round(subtotal + delivery_fee, 2)

    return jsonify({
        "success": True,
        "cart": cart,
        "subtotal": f"{subtotal:.2f}",
        "delivery_fee": f"{delivery_fee:.2f}",
        "total": f"{total:.2f}"
    })

# Function to update the cart
@app.route("/update_cart/<item_id>/<action>", methods=["POST"])
def update_cart(item_id, action):
    user_id = session["user_id"]

    if action == "increase":
        response = requests.post(
            f"{SERVER_URL}/cart",
            json={"user_id": user_id, "item_id": item_id, "action": "add"},
            timeout=REQUEST_TIMEOUT,
        )
    elif action == "decrease":
        # First, get the current cart correctly:
        cart_response = requests.get(
            f"{SERVER_URL}/cart",
            params={"user_id": user_id},
            timeout=REQUEST_TIMEOUT,
        )
        if cart_response.status_code != 200:
            return jsonify({"success": False, "error": "Failed to get cart"}), 500

        cart = cart_response.json()
        quantity = cart.get(item_id, {}).get("quantity", 0)

        if quantity > 1:
            response = requests.post(
                f"{SERVER_URL}/cart",
                json={"user_id": user_id, "item_id": item_id, "quantity": quantity - 1, "action": "update"},
                timeout=REQUEST_TIMEOUT,
            )
        elif quantity == 1:
            response = requests.post(
                f"{SERVER_URL}/cart",
                json={"user_id": user_id, "item_id": item_id, "action": "delete"},
                timeout=REQUEST_TIMEOUT,
            )
        else:
            return jsonify({"success": False, "error": "Item not in cart"}), 400
    else:
        return jsonify({"success": False, "error": "Invalid action"}), 400

    if response.status_code != 200:
        return jsonify({"success": False, "error": "Failed to update cart"}), 500

    # Fetch updated cart and items to return updated totals
    updated_cart_response = requests.get(
        f"{SERVER_URL}/cart",
        params={"user_id": user_id},
        timeout=REQUEST_TIMEOUT,
    )

    if updated_cart_response.status_code != 200:
        return jsonify({"success": False, "error": "Failed to fetch updated cart"}), 500

    updated_cart = updated_cart_response.json()

    items_response = requests.get(f"{SERVER_URL}/items", timeout=REQUEST_TIMEOUT)
    if items_response.status_code != 200:
        return jsonify({"success": False, "error": "Failed to fetch items"}), 500

    items = items_response.json()

    # Recalculate totals
    subtotal = sum(
        details.get("quantity", 0) * items.get(i_id, {}).get("price", 0)
        for i_id, details in updated_cart.items()
        if isinstance(details, dict)
    )
    delivery_fee = round(subtotal * DELIVERY_FEE_PERCENTAGE, 2)
    total = round(subtotal + delivery_fee, 2)

    return jsonify({
        "success": True,
        "cart": updated_cart,
        "subtotal": f"{subtotal:.2f}",
        "delivery_fee": f"{delivery_fee:.2f}",
        "total": f"{total:.2f}"
    }), 200

# –––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––
# Order Management
# –––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––

# Function to get the order details
@app.route("/order_details/<int:order_id>")
def order_details(order_id):
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
        for item_id, details in cart.items()
    )

    if "timestamp" in order and order["timestamp"]:
        order["timestamp"] = convert_to_est(order["timestamp"])

    return render_template(
        "order_details.html",
        order=order,
        subtotal=subtotal,
        username=current_username,
    )

# Function to place an order
@app.route("/place_order", methods=["POST"])
def place_order():
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

# Function to see the order status
@app.route("/order_status/<int:order_id>")
def order_status(order_id):
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT timeline FROM orders WHERE id = %s", (order_id,)
    )
    order = cursor.fetchone()
    conn.close()

    if not order:
        return jsonify({"error": "Order not found."}), 404

    timeline = json.loads(order["timeline"])
    return jsonify({"timeline": timeline})


# –––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––
# Delivery Management
# –––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––

# Function to accept a delivery
@app.route("/accept_delivery/<int:delivery_id>", methods=["POST"])
def accept_delivery(delivery_id):
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    update_order_claim_status(user_id, delivery_id)
    return redirect(
        url_for("deliverer_timeline", delivery_id=delivery_id)
    )

# Function to decline a delivery
@app.route(
    "/decline_delivery/<delivery_id>",
    methods=["POST"],
    endpoint="decline_delivery",
)
def decline_delivery_route(delivery_id):
    response = requests.post(
        f"{SERVER_URL}/decline_delivery/{delivery_id}",
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code == 200:
        return redirect(url_for("deliver"))
    return "Error declining delivery", response.status_code

# Function to update the checklist on timeline
@app.route("/update_checklist", methods=["POST"])
def update_checklist():
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
        "SELECT timeline, claimed_by FROM orders WHERE id = %s",
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
            jsonify({"success": False, "error": "Not authorized"}),
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
            timeline.get(steps[i], False)
            for i in range(step_index + 1, len(steps))
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

# Function to get the deliverer timeline
@app.route("/deliverer_timeline/<int:delivery_id>")
def deliverer_timeline(delivery_id):
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

# –––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––
# Utilities (Timezone, Ratings, Favorites)
# –––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––––

# Function to add an item to favorites
@app.route("/add_favorite/<item_id>", methods=["POST"])
def add_favorite(item_id):
    user_id = session.get("user_id")
    if not user_id:
        return (
            jsonify({"success": False, "error": "User not logged in"}),
            401,
        )

    logging.info(
        "Adding favorite: user_id=%s, item_id=%s", user_id, item_id
    )

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
    except Exception as e:
        logging.error("Error adding favorite: %s", str(e))
        return (
            jsonify({"success": False, "error": "Internal error."}),
            500,
        )
    finally:
        conn.close()

# Function to remove an item from favorites
@app.route("/remove_favorite/<item_id>", methods=["POST"])
def remove_favorite(item_id):
    user_id = session.get("user_id")
    if not user_id:
        return (
            jsonify({"success": False, "error": "User not logged in"}),
            401,
        )

    logging.info(
        "Removing favorite: user_id=%s, item_id=%s", user_id, item_id
    )

    conn = get_user_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM favorites WHERE user_id = %s AND item_id = %s",
            (user_id, item_id),
        )
        conn.commit()
        return jsonify({"success": True}), 200
    except Exception as e:
        logging.error("Error removing favorite: %s", str(e))
        return (
            jsonify({"success": False, "error": "Internal error."}),
            500,
        )
    finally:
        conn.close()

# Function to submit a rating
@app.route("/submit_rating", methods=["POST"])
def submit_rating():
    user_id = session.get("user_id")
    if not user_id:
        return (
            jsonify({"success": False, "error": "Not logged in"}),
            401,
        )

    data = request.get_json()
    rated_user_id = data.get("rated_user_id")
    rater_role = data.get("rater_role")
    rating = data.get("rating")
    order_id = data.get("order_id")

    if not (rated_user_id and rater_role and rating and order_id):
        return (
            jsonify({"success": False, "error": "Missing fields"}),
            400,
        )

    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
    order = cursor.fetchone()

    if not order:
        conn.close()
        return (
            jsonify({"success": False, "error": "Order not found"}),
            404,
        )

    timeline = json.loads(order["timeline"])
    if not timeline.get("Delivered"):
        conn.close()
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Cannot rate before order is delivered",
                }
            ),
            400,
        )

    # Authorization check
    if rater_role == "deliverer":
        if order["claimed_by"] != user_id:
            conn.close()
            return (
                jsonify({"success": False, "error": "Not authorized"}),
                403,
            )
    elif rater_role == "shopper":
        if order["user_id"] != user_id:
            conn.close()
            return (
                jsonify({"success": False, "error": "Not authorized"}),
                403,
            )
    else:
        conn.close()
        return jsonify({"success": False, "error": "Invalid role"}), 400

    # Update the rating in users table
    if not update_rating(rated_user_id, rater_role, int(rating)):
        conn.close()
        return (
            jsonify(
                {"success": False, "error": "Rating update failed"}
            ),
            500,
        )

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

    # Check if both have rated
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

    return (
        jsonify({"success": True, "redirect_url": url_for("home")}),
        200,
    )

# Function to convert a UTC datetime to EST
def convert_to_est(dt_utc):
    # dt_utc is already a datetime object with a UTC timezone or naive (assume UTC)
    if dt_utc.tzinfo is None:
        # If it's a naive datetime, attach UTC tzinfo
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)

    EST = timezone(timedelta(hours=-5))
    dt_est = dt_utc.astimezone(EST)
    return dt_est.strftime("%Y-%m-%d %H:%M EST")

if __name__ == "__main__":
    init_user_db()
    app.run(host="localhost", port=8000, debug=get_debug_mode())
