# 🚀 US Market Pulse - Quick Start Guide

## ONE-CLICK LAUNCH 🎯

### Option 1: Python Launcher (Recommended - Cross-platform)
```bash
python3 launch_dashboard.py
```

### Option 2: Shell Script (Mac/Linux)
```bash
./launch.sh
```

### Option 3: Direct Launch
```bash
./launch_dashboard.py
```

## What the Launcher Does

The automatic launcher handles **EVERYTHING** for you:

1. ✅ **Checks Python version** (requires 3.8+)
2. ✅ **Verifies all files** are present
3. ✅ **Checks dependencies** (Flask, yfinance, etc.)
4. ✅ **Auto-installs missing packages** if needed
5. ✅ **Starts the web server** on port 5000
6. ✅ **Opens your browser** automatically to http://localhost:5000

## First Time Setup

No setup needed! Just run:

```bash
python3 launch_dashboard.py
```

The launcher will automatically install any missing dependencies.

## Manual Installation (Optional)

If you prefer to install dependencies manually first:

```bash
pip install -r requirements_web.txt
```

Then launch:

```bash
python3 launch_dashboard.py
```

## Stopping the Server

Press `Ctrl+C` in the terminal to stop the dashboard.

## Troubleshooting

### "Command not found"
Make the script executable:
```bash
chmod +x launch_dashboard.py
```

### "Module not found" errors
The launcher should auto-install. If it fails, manually install:
```bash
pip install Flask Flask-SocketIO Flask-CORS python-socketio eventlet yfinance pandas numpy
```

### Port 5000 already in use
Kill existing process:
```bash
lsof -ti:5000 | xargs kill -9
```

Or edit `web_app.py` to use a different port (e.g., 5001).

## Accessing the Dashboard

Once launched, open your browser to:

- 🌐 **http://localhost:5000** (main dashboard)
- 📊 **http://localhost:5000/scanners** (trading scanners)
- 👁️ **http://localhost:5000/monitoring** (live positions)
- 📈 **http://localhost:5000/options** (options analysis)

## Production Deployment

For production servers, use:

```bash
gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:5000 web_app:app
```

---

**That's it!** One command launches everything. 🎉
