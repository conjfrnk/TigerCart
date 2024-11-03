#!/usr/bin/env python
"""
server.py
Serves data for the TigerCart app.
"""

from flask import Flask, jsonify, request
from config import get_debug_mode

app = Flask(__name__)
DELIVERY_FEE_PERCENTAGE = 0.1

# Temporary cart and item storage
cart = {}
sample_items = {
    "1": {"name": "Coke", "price": 1.09, "category": "drinks"},
    "2": {"name": "Diet Coke", "price": 1.29, "category": "drinks"},
    "3": {
        "name": "Tropicana Orange Juice",
        "price": 0.89,
        "category": "drinks",
    },
    "4": {
        "name": "Lay’s Potato Chips",
        "price": 1.59,
        "category": "food",
    },
    "5": {"name": "Snickers Bar", "price": 0.99, "category": "food"},
    "6": {"name": "Notebook", "price": 2.49, "category": "other"},
}

# Sample delivery data
deliveries = {
    "1": {
        "id": "1",
        "item_count": 5,
        "location": "Firestone Library, B-Floor",
        "delivery_items": [
            {"name": "Diet Coke", "price": 1.28, "quantity": 2},
            {
                "name": "Lay’s Potato Chips",
                "price": 1.59,
                "quantity": 1,
            },
        ],
    },
    "2": {
        "id": "2",
        "item_count": 1,
        "location": "Friend Center 001",
        "delivery_items": [
            {"name": "Coke", "price": 1.09, "quantity": 1}
        ],
    },
    "3": {
        "id": "3",
        "item_count": 20,
        "location": "Stadium Drive Garage",
        "delivery_items": [
            {"name": "Notebook", "price": 2.49, "quantity": 8},
            {"name": "Snickers Bar", "price": 0.99, "quantity": 12},
        ],
    },
}


@app.route("/items", methods=["GET"])
def get_items():
    """Return the sample items."""
    return jsonify(sample_items)


@app.route("/cart", methods=["GET", "POST"])
def manage_cart():
    """Return or update the cart data."""
    if request.method == "POST":
        cart_data = request.json
        item_id = cart_data.get("item_id")
        action = cart_data.get("action")

        if action == "add":
            # Initialize or increment quantity
            if item_id in cart:
                cart[item_id]["quantity"] += 1
            else:
                cart[item_id] = {"quantity": 1}
        elif action == "delete" and item_id in cart:
            cart.pop(item_id, None)
        elif action == "update":
            quantity = cart_data.get("quantity")
            cart[item_id] = {"quantity": quantity}

        return jsonify(cart)

    # On GET request, just return the current cart
    return jsonify(cart)


@app.route("/deliveries", methods=["GET"])
def get_deliveries():
    """Return all available deliveries."""
    return jsonify(deliveries)


@app.route("/delivery/<delivery_id>", methods=["GET"])
def get_delivery(delivery_id):
    """Return details of a specific delivery by ID, with calculated totals."""
    delivery = deliveries.get(delivery_id)
    if delivery:
        total = sum(
            item["price"] * item["quantity"]
            for item in delivery["delivery_items"]
        )
        delivery["total"] = f"{total:.2f}"
        delivery["earnings"] = (
            f"{(total * DELIVERY_FEE_PERCENTAGE):.2f}"
        )
        return jsonify(delivery)
    return jsonify({"error": "Delivery not found"}), 404


if __name__ == "__main__":
    app.run(port=5150, debug=get_debug_mode())
