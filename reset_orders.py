# reset_orders.py

from database import get_main_db_connection


def reset_orders():
    conn = get_main_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM orders")
    conn.commit()
    conn.close()
    print("All orders have been deleted successfully.")


if __name__ == "__main__":
    reset_orders()
