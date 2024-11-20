#!/usr/bin/env python
""" 
auth.py
Handles authentication with CAS and user sessions.
"""

import re
import ssl
from urllib import parse, request
from flask import Blueprint, session, redirect, render_template, abort
import flask
from database import get_user_db_connection

auth_bp = Blueprint("auth", __name__)
_CAS_URL = "https://fed.princeton.edu/cas/"


def strip_ticket(url):
    """Remove the CAS ticket parameter from the URL.

    Args:
        url: The URL to strip the ticket from

    Returns:
        str: URL with the ticket parameter removed
    """
    if url is None:
        return "something is badly wrong"
    url = re.sub(r"ticket=[^&]*&?", "", url)
    url = re.sub(r"\?&?$|&$", "", url)
    return url


def create_ssl_context():
    """Create an SSL context for development use.

    Returns:
        ssl.SSLContext: A configured SSL context for development
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def validate(ticket):
    """Validate a CAS login ticket with the server.

    Args:
        ticket: The CAS ticket to validate

    Returns:
        str: Username if validation successful, None otherwise
    """
    val_url = (
        _CAS_URL
        + "validate"
        + "?service="
        + parse.quote(strip_ticket(flask.request.url))
        + "&ticket="
        + parse.quote(ticket)
    )

    context = create_ssl_context()
    with request.urlopen(val_url, context=context) as flo:
        lines = flo.readlines()

    if len(lines) != 2:
        return None

    first_line = lines[0].decode("utf-8")
    second_line = lines[1].decode("utf-8")

    if not first_line.startswith("yes"):
        return None
    return second_line


def authenticate():
    """Authenticate the remote user and return their username.

    Returns:
        str: The authenticated username

    Note:
        This function will redirect to CAS login if needed.
    """
    if "username" in flask.session:
        return flask.session.get("username")

    ticket = flask.request.args.get("ticket")
    if ticket is None:
        login_url = (
            _CAS_URL + "login?service=" + parse.quote(flask.request.url)
        )
        abort(redirect(login_url))

    username = validate(ticket)
    if username is None:
        login_url = (
            _CAS_URL
            + "login?service="
            + parse.quote(strip_ticket(flask.request.url))
        )
        abort(redirect(login_url))

    username = username.strip()
    flask.session["username"] = username

    conn = get_user_db_connection()
    cursor = conn.cursor()

    user = cursor.execute(
        "SELECT user_id FROM users WHERE name = ?", (username,)
    ).fetchone()

    if user is None:
        cursor.execute(
            "INSERT INTO users (name) VALUES (?)", (username,)
        )
        conn.commit()

    user_id = username
    conn.close()

    flask.session["user_id"] = user_id
    return username


@auth_bp.route("/logoutapp", methods=["GET"])
def logoutapp():
    """Log out of the application.

    Returns:
        template: The logged out page
    """
    session.clear()
    return render_template("loggedout.html")


@auth_bp.route("/logoutcas", methods=["GET"])
def logoutcas():
    """Log out of the CAS session.

    Returns:
        redirect: Redirect to CAS logout page
    """
    session.clear()
    logout_url = _CAS_URL + "logout"
    return redirect(logout_url)
