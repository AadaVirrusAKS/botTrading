"""
Routes Package - Flask Blueprint registration.
"""

def register_blueprints(app):
    """Register all route blueprints with the Flask app."""
    # Auth (must be first — initializes Flask-Login)
    from routes.auth import auth_bp, init_auth
    init_auth(app)
    app.register_blueprint(auth_bp)

    from routes.dashboard import dashboard_bp
    from routes.scanners import scanners_bp
    from routes.options import options_bp
    from routes.monitoring import monitoring_bp
    from routes.technical import technical_bp
    from routes.ai_trading import ai_trading_bp
    from routes.autonomous import autonomous_bp
    from routes.crypto import crypto_bp
    from routes.ai_analysis import ai_analysis_bp
    from routes.paper_trading import paper_bp
    from routes.cache_admin import cache_bp
    from routes.pages import pages_bp
    from routes.alpaca import alpaca_bp
    from routes.daily_agent import daily_agent_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(scanners_bp)
    app.register_blueprint(options_bp)
    app.register_blueprint(monitoring_bp)
    app.register_blueprint(technical_bp)
    app.register_blueprint(ai_trading_bp)
    app.register_blueprint(autonomous_bp)
    app.register_blueprint(crypto_bp)
    app.register_blueprint(ai_analysis_bp)
    app.register_blueprint(paper_bp)
    app.register_blueprint(cache_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(alpaca_bp)
    app.register_blueprint(daily_agent_bp)

    print("  ✅ Registered 15 route blueprints (incl. auth)")
