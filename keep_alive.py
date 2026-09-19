"""
Minimal web server so an external uptime pinger (e.g. UptimeRobot) can hit this Repl
and keep it awake, matching the workflow you already had running. Import and call
keep_alive() once from bot.py before client.run(...) if you want this active.
"""
import base64
import os
import re
from flask import Flask, Response, abort, request
from threading import Thread

app = Flask("")


@app.route("/")
def home():
    return "Wargame bot is alive."


@app.route('/flags/<digest>.png')
def nation_flag_image(digest):
    if not re.fullmatch(r'[0-9a-f]{64}', digest): abort(404)
    import db
    with db.cursor() as c:
        c.execute('SELECT png_base64 FROM flag_assets WHERE digest=?', (digest,))
        row = c.fetchone()
    if not row: abort(404)
    response = Response(base64.b64decode(row['png_base64']), mimetype='image/png')
    response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.set_etag(digest)
    return response.make_conditional(request)


def _run():
    app.run(host="0.0.0.0", port=int(os.getenv('PORT', '8080')))


def keep_alive():
    Thread(target=_run).start()
