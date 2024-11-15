#!/usr/bin/env python
""" auth.py Authors: See below"""

# -----------------------------------------------------------------------
# auth.py
# Authors: Alex Halderman, Scott Karlin, Brian Kernighan, Bob Dondero
# -----------------------------------------------------------------------

import urllib.request
import urllib.parse
import re
import flask
import ssl
from top import app
from flask import Blueprint, session, redirect, render_template, request, abort
from database import get_user_db_connection
import urllib.request
import urllib.parse
import re
import ssl

# -----------------------------------------------------------------------

auth_bp = Blueprint('auth', __name__)
_CAS_URL = "https://fed.princeton.edu/cas/"
context = ssl._create_unverified_context()
# -----------------------------------------------------------------------

# Return url after stripping out the "ticket" parameter that was
# added by the CAS server.


def strip_ticket(url):
    """see above for fxn definition"""
    if url is None:
        return "something is badly wrong"
    url = re.sub(r"ticket=[^&]*&?", "", url)
    url = re.sub(r"\?&?$|&$", "", url)
    return url


# -----------------------------------------------------------------------

# Validate a login ticket by contacting the CAS server. If
# valid, return the user's username; otherwise, return None.


def validate(ticket):
    """see above for fxn definition"""
    val_url = (
        _CAS_URL
        + "validate"
        + "?service="
        + urllib.parse.quote(strip_ticket(flask.request.url))
        + "&ticket="
        + urllib.parse.quote(ticket)
    )
    lines = []
    with urllib.request.urlopen(val_url, context=context) as flo:
        lines = flo.readlines()  # Should return 2 lines.
    if len(lines) != 2:
        return None
    first_line = lines[0].decode("utf-8")
    second_line = lines[1].decode("utf-8")
    if not first_line.startswith("yes"):
        return None
    return second_line


# -----------------------------------------------------------------------

# Authenticate the remote user, and return the user's username.
# Do not return unless the user is successfully authenticated.


def authenticate():
    """Authenticate the remote user, and return the user's username."""
    # If the username is in the session, then the user was
    # authenticated previously. So return the username.
    if "username" in flask.session:
        return flask.session.get("username")

    # If the request does not contain a login ticket, then redirect
    # the browser to the login page to get one.
    ticket = flask.request.args.get("ticket")
    if ticket is None:
        login_url = (
            _CAS_URL
            + "login?service="
            + urllib.parse.quote(flask.request.url)
        )
        flask.abort(flask.redirect(login_url))

    # If the login ticket is invalid, then redirect the browser
    # to the login page to get a new one.
    username = validate(ticket)
    if username is None:
        login_url = (
            _CAS_URL
            + "login?service="
            + urllib.parse.quote(strip_ticket(flask.request.url))
        )
        flask.abort(flask.redirect(login_url))

    # The user is authenticated, so store the username in the session.
    username = username.strip()
    flask.session["username"] = username

    # Now, retrieve or create the user_id in the database
    conn = get_user_db_connection()
    cursor = conn.cursor()

    user = cursor.execute("SELECT user_id FROM users WHERE name = ?", (username,)).fetchone()

    if user is None:
        # User does not exist, create a new user
        cursor.execute("INSERT INTO users (name) VALUES (?)", (username,))
        conn.commit()
        user_id = cursor.lastrowid
    else:
        user_id = user['user_id']

    conn.close()

    # Store user_id in the session
    flask.session['user_id'] = user_id

    return username


# -----------------------------------------------------------------------


@auth_bp.route("/logoutapp", methods=["GET"])
def logoutapp():
    """Log out of the application."""
    session.clear()
    return render_template("loggedout.html")


# -----------------------------------------------------------------------


# auth.py
@auth_bp.route("/logoutcas", methods=["GET"])
def logoutcas():
    """Log out of the CAS session."""
    session.clear()
    logout_url = _CAS_URL + "logout"
    return redirect(logout_url)

