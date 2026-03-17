#!/bin/bash

# US Market Pulse - Quick Launcher Script
# One-click startup for the trading dashboard

echo ""
echo "================================================================================"
echo "🇺🇸  US MARKET PULSE - TRADING DASHBOARD LAUNCHER"
echo "================================================================================"
echo ""

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 not found. Please install Python 3.8 or higher."
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"
echo ""

# Change to project root directory
cd "$(dirname "$0")/.."

# Run the Python launcher
echo "🚀 Starting dashboard launcher..."
echo ""

python3 scripts/launch_dashboard.py

# Exit with the same code as the Python script
exit $?
