#!/bin/bash
# Start Trading Dashboard Web Server

cd "$(dirname "$0")/.."

echo "=============================================="
echo "🚀 Starting Trading Dashboard Web Server"
echo "=============================================="
echo ""
echo "✅ Using Python: .venv/bin/python"
echo "✅ Server will run on: http://localhost:5000"
echo ""
echo "📊 Dashboard will be available at:"
echo "   - Main Dashboard: http://localhost:5000/"
echo "   - Monitoring:     http://localhost:5000/monitoring"
echo "   - Scanners:       http://localhost:5000/scanners"
echo "   - Options:        http://localhost:5000/options"
echo ""
echo "⚠️  Press Ctrl+C to stop the server"
echo "=============================================="
echo ""

.venv/bin/python run.py
