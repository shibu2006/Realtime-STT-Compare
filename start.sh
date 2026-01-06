#!/bin/bash

# VoiceSearch App Startup Script
# This script starts the Flask-SocketIO application with optional ngrok tunneling
#
# Usage:
#   ./start.sh           - Start the app locally on http://localhost:8000
#   ./start.sh --ngrok   - Start the app with ngrok tunnel (public URL)
#   ./start.sh -n        - Short form for ngrok option
#   ./start.sh --help    - Show this help message

set -e

# Show help if requested
if [ "$1" == "--help" ] || [ "$1" == "-h" ]; then
    echo "VoiceSearch App Startup Script"
    echo ""
    echo "Usage:"
    echo "  ./start.sh              Start the app locally on http://localhost:8000"
    echo "  ./start.sh --ngrok      Start the app with ngrok tunnel (public URL)"
    echo "  ./start.sh -n           Short form for ngrok option"
    echo "  ./start.sh --help       Show this help message"
    echo ""
    echo "Requirements:"
    echo "  - Python 3.x"
    echo "  - Virtual environment (will be created if missing)"
    echo "  - .env file with DEEPGRAM_API_KEY"
    echo "  - ngrok (required only if using --ngrok option)"
    echo ""
    echo "Note:"
    echo "  Free ngrok accounts are limited to 1 simultaneous session."
    echo "  If you get an error about existing sessions, stop them first:"
    echo "    pkill ngrok"
    echo "  Or check running sessions:"
    echo "    ps aux | grep ngrok"
    echo ""
    exit 0
fi

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configuration
APP_PORT=8000
APP_FILE="voicesearch_app.py"
NGROK_PID_FILE=".ngrok.pid"
APP_PID_FILE=".app.pid"

# Function to print colored messages
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to cleanup on exit
cleanup() {
    print_info "Cleaning up..."
    
    # Kill ngrok if running (only if we started it)
    if [ -f "$NGROK_PID_FILE" ]; then
        NGROK_PID=$(cat "$NGROK_PID_FILE")
        if ps -p "$NGROK_PID" > /dev/null 2>&1; then
            # Check if this is a process we started (not an existing one)
            if pgrep -f "ngrok http $APP_PORT" | grep -q "$NGROK_PID"; then
                print_info "Stopping ngrok (PID: $NGROK_PID)..."
                kill "$NGROK_PID" 2>/dev/null || true
            else
                print_info "Leaving existing ngrok session running (PID: $NGROK_PID)"
            fi
        fi
        rm -f "$NGROK_PID_FILE"
    fi
    
    # Kill Flask app if running
    if [ -f "$APP_PID_FILE" ]; then
        APP_PID=$(cat "$APP_PID_FILE")
        if ps -p "$APP_PID" > /dev/null 2>&1; then
            print_info "Stopping Flask app (PID: $APP_PID)..."
            kill "$APP_PID" 2>/dev/null || true
        fi
        rm -f "$APP_PID_FILE"
    fi
    
    # Clean up ngrok URL file
    rm -f .ngrok_url
    
    print_success "Cleanup complete"
    exit 0
}

# Set up trap to cleanup on script exit
trap cleanup EXIT INT TERM

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    print_error "Python3 is not installed or not in PATH"
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    print_warning "Virtual environment not found. Creating one..."
    python3 -m venv venv
    print_success "Virtual environment created"
fi

# Activate virtual environment
print_info "Activating virtual environment..."
source venv/bin/activate

# Check if requirements are installed
if ! python3 -c "import flask" 2>/dev/null; then
    print_warning "Dependencies not installed. Installing requirements..."
    pip install -q -r requirements.txt
    print_success "Dependencies installed"
fi

# Check if .env file exists
if [ ! -f ".env" ]; then
    print_warning ".env file not found!"
    print_info "Please create a .env file with your DEEPGRAM_API_KEY"
    print_info "You can use sample.env as a template (found in archive folder)"
    exit 1
fi

# Check if ngrok is requested
USE_NGROK=false
if [ "$1" == "--ngrok" ] || [ "$1" == "-n" ]; then
    USE_NGROK=true
    
    # Check if ngrok is installed
    if ! command -v ngrok &> /dev/null; then
        print_error "ngrok is not installed or not in PATH"
        print_info "Install ngrok from: https://ngrok.com/download"
        exit 1
    fi
    
    # Check if ngrok is already running
    EXISTING_NGROK=$(pgrep -f "ngrok http" || true)
    if [ -n "$EXISTING_NGROK" ]; then
        print_warning "ngrok is already running (PID: $EXISTING_NGROK)"
        print_info "Attempting to reuse existing ngrok session..."
        
        # Try to get URL from existing ngrok session
        sleep 2
        NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if data.get('tunnels'):
        for tunnel in data['tunnels']:
            if tunnel.get('public_url', '').startswith('https://'):
                print(tunnel['public_url'])
                break
except:
    pass
" 2>/dev/null || echo "")
        
        if [ -n "$NGROK_URL" ]; then
            # Check if it's pointing to the right port
            TUNNEL_PORT=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if data.get('tunnels'):
        addr = data['tunnels'][0].get('config', {}).get('addr', '')
        if ':' in addr:
            print(addr.split(':')[-1])
except:
    pass
" 2>/dev/null || echo "")
            
            if [ "$TUNNEL_PORT" == "$APP_PORT" ]; then
                print_success "Reusing existing ngrok tunnel on port $APP_PORT"
                echo "$EXISTING_NGROK" > "$NGROK_PID_FILE"
                USE_EXISTING_NGROK=true
            else
                print_warning "Existing ngrok tunnel is on port $TUNNEL_PORT, but app needs port $APP_PORT"
                print_info "To stop existing ngrok session, run:"
                print_info "  kill $EXISTING_NGROK"
                print_info "Or check ngrok dashboard: http://localhost:4040"
                exit 1
            fi
        else
            print_warning "Could not connect to existing ngrok session"
            print_info "You may need to stop existing ngrok processes first:"
            print_info "  kill $EXISTING_NGROK"
            print_info "Or check if ngrok is running on a different port"
            exit 1
        fi
    else
        print_info "Starting ngrok tunnel on port $APP_PORT..."
        USE_EXISTING_NGROK=false
        
        # Start ngrok in background
        ngrok http $APP_PORT > /tmp/ngrok.log 2>&1 &
        NGROK_PID=$!
        echo $NGROK_PID > "$NGROK_PID_FILE"
        
        # Wait for ngrok to start
        sleep 3
        
        # Check if ngrok is still running
        if ! ps -p "$NGROK_PID" > /dev/null 2>&1; then
            print_error "Failed to start ngrok"
            print_info "Error details from /tmp/ngrok.log:"
            tail -20 /tmp/ngrok.log 2>/dev/null | sed 's/^/  /'
            echo ""
            print_info "Common issues:"
            print_info "  - ngrok authentication required (run: ngrok config add-authtoken <token>)"
            print_info "  - Another ngrok session is already running"
            print_info "  - Port $APP_PORT is already in use"
            exit 1
        fi
    fi
    
    # Get ngrok public URL (if not already fetched)
    if [ -z "$NGROK_URL" ] || [ "$USE_EXISTING_NGROK" != "true" ]; then
        print_info "Fetching ngrok public URL..."
        sleep 2
        
        # Try to get URL from ngrok API using Python (more reliable)
        NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if data.get('tunnels'):
        for tunnel in data['tunnels']:
            if tunnel.get('public_url', '').startswith('https://'):
                print(tunnel['public_url'])
                break
except:
    pass
" 2>/dev/null || echo "")
        
        # Fallback: try grep method
        if [ -z "$NGROK_URL" ]; then
            NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | grep -o '"public_url":"https://[^"]*' | head -1 | cut -d'"' -f4)
        fi
    fi
    
    if [ -n "$NGROK_URL" ]; then
        # Save URL to file for easy access
        echo "$NGROK_URL" > .ngrok_url
        print_success "ngrok tunnel established!"
        echo ""
        echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
        echo -e "${GREEN}  🌐 Public URL: ${BLUE}$NGROK_URL${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
        echo ""
        print_info "You can access your app at: $NGROK_URL"
        print_info "ngrok web interface: http://localhost:4040"
        print_info "URL saved to: .ngrok_url"
    else
        print_warning "Could not automatically fetch ngrok URL"
        print_info "Check ngrok web interface at: http://localhost:4040"
        print_info "Or check /tmp/ngrok.log for details"
        print_info "You can also run: curl http://localhost:4040/api/tunnels"
    fi
fi

# Start Flask application
print_info "Starting VoiceSearch application on port $APP_PORT..."
print_info "Press Ctrl+C to stop the application"

if [ "$USE_NGROK" = true ]; then
    print_info "App will be accessible via ngrok tunnel"
else
    print_info "App will be accessible at: http://localhost:$APP_PORT"
fi

echo ""

# Start the Flask app
python3 "$APP_FILE" &
APP_PID=$!
echo $APP_PID > "$APP_PID_FILE"

# Wait a moment for the app to start
sleep 2

# Check if the app is still running
if ! ps -p "$APP_PID" > /dev/null 2>&1; then
    print_error "Failed to start Flask application"
    exit 1
fi

print_success "VoiceSearch application started (PID: $APP_PID)"

# Display connection info
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
if [ "$USE_NGROK" = true ]; then
    echo -e "${GREEN}  ✅ Application is running${NC}"
    echo -e "${GREEN}  🌐 Public URL: ${BLUE}$NGROK_URL${NC}"
    echo -e "${GREEN}  📊 ngrok Dashboard: ${BLUE}http://localhost:4040${NC}"
else
    echo -e "${GREEN}  ✅ Application is running${NC}"
    echo -e "${GREEN}  🔗 Local URL: ${BLUE}http://localhost:$APP_PORT${NC}"
fi
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Wait for the Flask app process
wait $APP_PID
