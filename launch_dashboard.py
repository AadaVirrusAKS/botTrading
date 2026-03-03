#!/usr/bin/env python3
"""
🚀 US MARKET PULSE - ONE-CLICK LAUNCHER
=====================================
Automatically checks dependencies, installs if needed, and launches the web dashboard.

Usage:
    python3 launch_dashboard.py
    
Or make executable:
    chmod +x launch_dashboard.py
    ./launch_dashboard.py
"""

import subprocess
import sys
import os
import time

# Color codes for terminal output
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
BLUE = '\033[94m'
BOLD = '\033[1m'
RESET = '\033[0m'

def print_banner():
    """Display startup banner"""
    print("\n" + "="*80)
    print(f"{BOLD}{BLUE}🇺🇸  US MARKET PULSE - TRADING DASHBOARD{RESET}")
    print("="*80 + "\n")

def check_python_version():
    """Ensure Python 3.8+"""
    if sys.version_info < (3, 8):
        print(f"{RED}❌ Error: Python 3.8 or higher required{RESET}")
        print(f"   Current version: {sys.version}")
        sys.exit(1)
    print(f"{GREEN}✅ Python {sys.version_info.major}.{sys.version_info.minor} detected{RESET}")

def check_and_install_dependencies():
    """Check if required packages are installed, install if missing"""
    print(f"\n{YELLOW}📦 Checking dependencies...{RESET}")
    
    required_packages = {
        'flask': 'Flask',
        'flask_socketio': 'Flask-SocketIO',
        'flask_cors': 'Flask-CORS',
        'socketio': 'python-socketio',
        'eventlet': 'eventlet',
        'yfinance': 'yfinance',
        'pandas': 'pandas',
        'numpy': 'numpy'
    }
    
    missing_packages = []
    
    for module_name, package_name in required_packages.items():
        try:
            __import__(module_name)
            print(f"{GREEN}  ✓ {package_name}{RESET}")
        except ImportError:
            print(f"{RED}  ✗ {package_name} (missing){RESET}")
            missing_packages.append(package_name)
    
    if missing_packages:
        print(f"\n{YELLOW}📥 Installing missing packages...{RESET}")
        print(f"   Packages to install: {', '.join(missing_packages)}\n")
        
        try:
            # Try using requirements_web.txt first
            if os.path.exists('requirements_web.txt'):
                subprocess.check_call([
                    sys.executable, '-m', 'pip', 'install', '-r', 'requirements_web.txt', '-q'
                ])
            else:
                # Install individually
                subprocess.check_call([
                    sys.executable, '-m', 'pip', 'install', 
                    'Flask', 'Flask-SocketIO', 'Flask-CORS', 'python-socketio', 
                    'eventlet', 'yfinance', 'pandas', 'numpy', '-q'
                ])
            print(f"{GREEN}✅ All dependencies installed successfully!{RESET}")
        except subprocess.CalledProcessError as e:
            print(f"{RED}❌ Failed to install dependencies: {e}{RESET}")
            print(f"\n{YELLOW}Please install manually:{RESET}")
            print(f"   pip install -r requirements_web.txt")
            sys.exit(1)
    else:
        print(f"{GREEN}✅ All dependencies satisfied!{RESET}")

def check_web_app_exists():
    """Verify web_app.py exists"""
    if not os.path.exists('web_app.py'):
        print(f"{RED}❌ Error: web_app.py not found in current directory{RESET}")
        print(f"   Current directory: {os.getcwd()}")
        sys.exit(1)
    print(f"{GREEN}✅ Web application found{RESET}")

def check_templates_exist():
    """Verify template files exist"""
    if not os.path.exists('templates'):
        print(f"{YELLOW}⚠️  Warning: templates/ directory not found{RESET}")
        print(f"   Some pages may not load correctly")
    else:
        print(f"{GREEN}✅ Templates directory found{RESET}")

def open_browser(url, delay=3):
    """Open browser to the dashboard URL after a delay"""
    import webbrowser
    import threading
    
    def delayed_open():
        time.sleep(delay)
        print(f"\n{BLUE}🌐 Opening browser to {url}...{RESET}")
        try:
            webbrowser.open(url)
        except Exception as e:
            print(f"{YELLOW}   Could not auto-open browser: {e}{RESET}")
            print(f"   Please open manually: {url}")
    
    thread = threading.Thread(target=delayed_open, daemon=True)
    thread.start()

def launch_web_app():
    """Start the Flask web application"""
    print(f"\n{YELLOW}🚀 Starting web server...{RESET}\n")
    print("="*80)
    print(f"{BOLD}Dashboard will be available at:{RESET}")
    print(f"{GREEN}   🌐 http://localhost:5000{RESET}")
    print(f"{GREEN}   🌐 http://127.0.0.1:5000{RESET}")
    print("="*80)
    print(f"\n{YELLOW}📝 Server logs:{RESET}\n")
    
    # Auto-open browser after 3 seconds
    open_browser('http://localhost:5000', delay=3)
    
    try:
        # Run the web app
        subprocess.check_call([sys.executable, 'web_app.py'])
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}🛑 Server stopped by user{RESET}")
        print(f"{GREEN}✅ Dashboard shutdown complete{RESET}\n")
    except subprocess.CalledProcessError as e:
        print(f"\n{RED}❌ Server error: {e}{RESET}")
        sys.exit(1)

def main():
    """Main launcher function"""
    print_banner()
    
    print(f"{BOLD}Step 1/5: Checking Python version{RESET}")
    check_python_version()
    
    print(f"\n{BOLD}Step 2/5: Verifying application files{RESET}")
    check_web_app_exists()
    check_templates_exist()
    
    print(f"\n{BOLD}Step 3/5: Checking dependencies{RESET}")
    check_and_install_dependencies()
    
    print(f"\n{BOLD}Step 4/5: Preparing to launch{RESET}")
    print(f"{GREEN}✅ Pre-flight checks complete{RESET}")
    
    print(f"\n{BOLD}Step 5/5: Launching dashboard{RESET}")
    
    print(f"\n{BLUE}💡 TIP: Press Ctrl+C to stop the server{RESET}")
    time.sleep(1)
    
    launch_web_app()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}🛑 Launch cancelled by user{RESET}\n")
        sys.exit(0)
    except Exception as e:
        print(f"\n{RED}❌ Unexpected error: {e}{RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
