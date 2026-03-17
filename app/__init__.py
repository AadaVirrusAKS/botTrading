"""
App Package - Flask web application for the trading dashboard.
"""
import os
import sys

# Ensure project root is on sys.path for imports (routes, services, config, etc.)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
