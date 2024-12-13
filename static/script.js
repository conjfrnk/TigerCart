// This file contains JavaScript code that is used to interact with the server

// Function to add an item to the cart
document.addEventListener('DOMContentLoaded', () => {
    document.body.addEventListener('click', function(event) {
        if (event.target.classList.contains('add-to-cart')) {
            const itemId = event.target.getAttribute('data-item-id');
            addToCart(itemId);
        }
    });
});

// Function to update the quantity of an item in the cart
function updateQuantity(itemId, action) {
    fetch(`/update_cart/${itemId}/${action}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ user_id: userId })  // Ensure userId is globally defined
    })
    .then(response => response.json())
    .then(cart => {
        location.reload();  // Refresh to reflect the updated cart view
    })
    .catch(error => {
        console.error('Error updating quantity:', error);
        alert('Failed to update item quantity.');
    });
}

// Function to delete an item from the cart
function deleteItem(itemId) {
    if (confirm('Are you sure that you want to remove this item from your cart?')) {
        fetch(`/delete_item/${itemId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ user_id: userId })
        }).then(response => response.json())
          .then(cart => location.reload());  // Refresh to reflect updated cart view
    }
}

// Function to place an order
function placeOrder(itemsInCart) {
    if (itemsInCart === 0) {
        alert('No items in cart, please go back and make an order!');
        return;
    }

    // Prompt the user for a delivery location
    const deliveryLocation = prompt("Please enter the delivery location:");

    // If a location is provided, proceed with placing the order
    if (deliveryLocation) {
        fetch('/place_order', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ delivery_location: deliveryLocation })
        })
        .then(response => {
            if (response.ok) {
                alert('Order placed successfully!');
                window.location.href = '/shopper_timeline';
            } else {
                alert('Failed to place the order.');
            }
        });
    } else {
        alert("Delivery location is required to place an order.");
    }
}

// Function to accept a delivery
function acceptDelivery(deliveryId) {
    fetch(`/accept_delivery/${deliveryId}`, { method: 'POST' })
        .then(response => {
            if (response.ok) {
                alert('Delivery accepted');
                window.location.href = '/deliverer_timeline';
            } else {
                alert('Failed to accept the delivery');
            }
        })
        .catch(error => console.error('Error accepting delivery:', error));
}

// Function to decline a delivery
function declineDelivery(deliveryId) {
    fetch(`/decline_delivery/${deliveryId}`, { method: 'POST' })
        .then(response => {
            if (response.ok) {
                alert('Delivery declined');
                window.location.href = '/deliver';  // Redirect back to deliveries page
            } else {
                alert('Failed to decline the delivery');
            }
        })
        .catch(error => console.error('Error declining delivery:', error));
}

// Function to mark a step as complete in timeline
function markStepComplete(stepId) {
    const stepElement = document.getElementById(stepId);
    const circleElement = stepElement.querySelector('.circle');
    const buttonElement = stepElement.querySelector('button');

    // Change circle appearance and button status
    circleElement.classList.add('complete');
    buttonElement.textContent = 'Completed';
    buttonElement.style.backgroundColor = 'green';
}
