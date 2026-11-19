"""HTTP blueprints.

Each module in this package exposes a ``bp`` Blueprint. Blueprints are added
as build phases are implemented and registered here.
"""


def register_blueprints(app):
    """Register every blueprint on ``app``."""
    from routes.appointments import bp as appointments_bp
    from routes.auth import bp as auth_bp
    from routes.providers import bp as providers_bp
    from routes.reports import bp as reports_bp
    from routes.services import bp as services_bp
    from routes.slots import bp as slots_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(services_bp)
    app.register_blueprint(providers_bp)
    app.register_blueprint(slots_bp)
    app.register_blueprint(appointments_bp)
    app.register_blueprint(reports_bp)
