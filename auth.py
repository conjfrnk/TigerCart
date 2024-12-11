#!/usr/bin/env python
"""
auth.py
Handles authentication with CAS and user sessions.
Adopted to PostgreSQL (replaced ? with %s in queries).
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
    if url is None:
        return "something is badly wrong"
    url = re.sub(r"ticket=[^&]*&?", "", url)
    url = re.sub(r"\?&?$|&$", "", url)
    return url


def create_ssl_context():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def validate(ticket):
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

    cursor.execute(
        "SELECT user_id FROM users WHERE name = %s", (username,)
    )
    user = cursor.fetchone()

    if user is None:
        # Insert user if not exists
        cursor.execute(
            "INSERT INTO users (user_id, name) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
            (username, username),
        )
        conn.commit()

    user_id = username
    conn.close()

    flask.session["user_id"] = user_id
    return username


@auth_bp.route("/logoutapp", methods=["GET"])
def logoutapp():
    session.clear()
    return render_template("loggedout.html")


@auth_bp.route("/logoutcas", methods=["GET"])
def logoutcas():
    session.clear()
    logout_url = _CAS_URL + "logout"
    return redirect(logout_url)
