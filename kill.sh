#!/bin/sh
doas /usr/bin/pkill -f "gunicorn --bind 127.0.0.1:8000"
doas /usr/bin/pkill -f "gunicorn --bind 127.0.0.1:5150"
