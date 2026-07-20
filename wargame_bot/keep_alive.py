"""
Minimal web server so an external uptime pinger (e.g. UptimeRobot) can hit this Repl
and keep it awake, matching the workflow you already had running. Import and call
keep_alive() once from bot.py before client.run(...) if you want this active.
"""
from flask import Flask
from threading import Thread

app = Flask("")


@app.route("/")
def home():
    return "Wargame bot is alive."


def _run():
    app.run(host="0.0.0.0", port=8080)


def keep_alive():
    Thread(target=_run).start()
