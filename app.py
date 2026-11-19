"""Application factory and entry point."""

from flask import Flask, jsonify, request

import db as database
from config import Config
from routes import register_blueprints
from services_logic.notifications import register_cli


def create_app(test_config=None):
    # static_url_path="" serves files from static/ at the site root, so the
    # frontend can use /js/app.js instead of /static/js/app.js.
    app = Flask(__name__, static_folder="static", static_url_path="")
    app.config.from_object(Config)
    # Tests pass a temporary DB path and a test secret key here.
    if test_config is not None:
        app.config.update(test_config)

    database.init_app(app)
    register_blueprints(app)
    register_cli(app)

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.errorhandler(404)
    def not_found(error):
        # API clients expect JSON; normal browser requests keep Flask's HTML.
        if request.path.startswith("/api/"):
            return jsonify({"error": "The requested resource was not found."}), 404
        return error

    return app


# Module-level app for `flask run` and WSGI servers, which look for `app`.
app = create_app()


def _bootstrap():
    # init_db and seed are both idempotent, so this is safe on every start.
    with app.app_context():
        database.init_db()
        database.seed()


if __name__ == "__main__":
    _bootstrap()
    app.run(debug=True)
