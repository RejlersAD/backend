#!/bin/bash
###############################################################################
# RAD AI Attendance Sync Agent - Health Check & Restart Script (Linux)
###############################################################################
# Description:
#   Checks the status of the RAD AI attendance sync agent and restarts it if needed.
#   Can be run manually or via cron for automatic recovery.
#
# Usage:
#   ./restart_sync_agent.sh              # Check and restart if needed
#   ./restart_sync_agent.sh --check      # Only check status
#   ./restart_sync_agent.sh --force      # Force restart
#   ./restart_sync_agent.sh --hours 72   # Sync last 72 hours
#
# Author: RAD AI DevOps
# Date: 2026-07-22
# Version: 1.0
###############################################################################

set -euo pipefail

# Configuration
SERVICE_NAME="${SERVICE_NAME:-radai-attendance-sync}"
AGENT_PATH="${AGENT_PATH:-/opt/radai/sync-agent/timesheet_mirror_sync.py}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SYNC_HOURS="${SYNC_HOURS:-48}"
RAILWAY_URL="${RAILWAY_BACKEND_URL:-https://aiflowbackend-production.up.railway.app}"

# Flags
CHECK_ONLY=false
FORCE_RESTART=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --check|-c)
            CHECK_ONLY=true
            shift
            ;;
        --force|-f)
            FORCE_RESTART=true
            shift
            ;;
        --hours|-h)
            SYNC_HOURS="$2"
            shift 2
            ;;
        --service|-s)
            SERVICE_NAME="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --check, -c          Only check status, don't restart"
            echo "  --force, -f          Force restart even if healthy"
            echo "  --hours, -h HOURS    Number of hours to sync (default: 48)"
            echo "  --service, -s NAME   Service name (default: radai-attendance-sync)"
            echo "  --help               Show this help message"
            echo ""
            echo "Environment Variables:"
            echo "  SERVICE_NAME                Service name (default: radai-attendance-sync)"
            echo "  AGENT_PATH                  Path to sync agent script"
            echo "  PYTHON_BIN                  Python executable (default: python3)"
            echo "  RAILWAY_BACKEND_URL         Railway backend URL"
            echo "  TIMESHEET_MIRROR_API_KEY    API key for sync agent"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Helper functions
print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_error() { echo -e "${RED}❌ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
print_info() { echo -e "${CYAN}ℹ️  $1${NC}"; }

# Header
echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║       RAD AI Attendance Sync Agent - Health Check            ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# Check if running as root/sudo
if [[ $EUID -ne 0 ]] && command -v systemctl &> /dev/null; then
    print_warning "Not running as root. Some operations may require sudo."
    SUDO="sudo"
else
    SUDO=""
fi

# 1. Check systemd service
print_info "Checking systemd service: $SERVICE_NAME..."
if command -v systemctl &> /dev/null; then
    if systemctl list-units --all | grep -q "$SERVICE_NAME"; then
        print_success "Service found: $SERVICE_NAME"
        
        # Get service status
        if $SUDO systemctl is-active --quiet "$SERVICE_NAME"; then
            SERVICE_STATUS="active"
            print_success "Service is running"
        else
            SERVICE_STATUS="inactive"
            print_warning "Service is NOT running"
        fi
        
        # Get service info
        if $SUDO systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
            print_info "   Enabled: yes (starts on boot)"
        else
            print_warning "   Enabled: no (won't start on boot)"
        fi
        
        # Show last few log lines
        print_info "   Recent logs:"
        $SUDO journalctl -u "$SERVICE_NAME" -n 3 --no-pager 2>/dev/null | tail -n 3 | sed 's/^/   /'
        
    else
        print_error "Service not found: $SERVICE_NAME"
        print_info "Create service file: /etc/systemd/system/$SERVICE_NAME.service"
        SERVICE_STATUS="not_found"
    fi
else
    print_warning "systemctl not found - checking process manually"
    SERVICE_STATUS="unknown"
fi

# 2. Check sync agent script
echo ""
print_info "Checking sync agent script..."
if [[ -f "$AGENT_PATH" ]]; then
    print_success "Script found: $AGENT_PATH"
    SCRIPT_SIZE=$(stat -f%z "$AGENT_PATH" 2>/dev/null || stat -c%s "$AGENT_PATH" 2>/dev/null || echo "unknown")
    print_info "   Size: $SCRIPT_SIZE bytes"
    
    if [[ ! -x "$AGENT_PATH" ]]; then
        print_warning "   Script is not executable (chmod +x recommended)"
    fi
else
    print_error "Script not found: $AGENT_PATH"
    print_info "Restore the script or update AGENT_PATH environment variable"
    exit 1
fi

# 3. Check Python
echo ""
print_info "Checking Python..."
if command -v "$PYTHON_BIN" &> /dev/null; then
    PYTHON_VERSION=$($PYTHON_BIN --version 2>&1)
    print_success "Python found: $PYTHON_VERSION"
    print_info "   Path: $(which $PYTHON_BIN)"
else
    print_error "Python not found: $PYTHON_BIN"
    print_info "Install Python 3.8+ or update PYTHON_BIN environment variable"
    exit 1
fi

# 4. Check Railway backend connectivity
echo ""
print_info "Checking Railway backend connectivity..."
if command -v curl &> /dev/null; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 10 "$RAILWAY_URL/api/v1/health/" || echo "000")
    if [[ "$HTTP_CODE" == "200" ]]; then
        print_success "Railway backend is reachable"
        print_info "   URL: $RAILWAY_URL"
    else
        print_error "Railway backend returned HTTP $HTTP_CODE"
        print_info "Check network connection and URL: $RAILWAY_URL"
    fi
else
    print_warning "curl not found - skipping connectivity check"
fi

# 5. Check API key
echo ""
print_info "Checking API key configuration..."
if [[ -n "${TIMESHEET_MIRROR_API_KEY:-}" ]]; then
    KEY_LENGTH=${#TIMESHEET_MIRROR_API_KEY}
    KEY_PREVIEW="${TIMESHEET_MIRROR_API_KEY:0:8}..."
    print_success "API key configured: $KEY_PREVIEW (length: $KEY_LENGTH)"
else
    print_error "TIMESHEET_MIRROR_API_KEY environment variable not set"
    print_info "Set in service file or /etc/environment"
fi

# 6. Check if process is running
echo ""
print_info "Checking if sync agent process is running..."
PROCESS_COUNT=$(pgrep -f "timesheet_mirror_sync" | wc -l)

if [[ $PROCESS_COUNT -gt 0 ]]; then
    print_success "Sync agent process is running ($PROCESS_COUNT process(es))"
    
    # Show process info
    PIDS=$(pgrep -f "timesheet_mirror_sync" | tr '\n' ' ')
    print_info "   PID(s): $PIDS"
    
    # Show runtime
    if command -v ps &> /dev/null; then
        for pid in $PIDS; do
            RUNTIME=$(ps -p "$pid" -o etime= 2>/dev/null || echo "unknown")
            print_info "   Runtime (PID $pid): $RUNTIME"
        done
    fi
    
    if $FORCE_RESTART; then
        print_warning "Force flag set - will restart anyway"
    elif ! $CHECK_ONLY; then
        print_info "Agent is running. Use --force to restart anyway."
        exit 0
    fi
else
    print_warning "Sync agent process is NOT running"
fi

# 7. Decision: Restart or not?
echo ""
echo "═══════════════════════════════════════════════════════════════"

if $CHECK_ONLY; then
    print_info "Check mode - no restart performed"
    exit 0
fi

if [[ ! $FORCE_RESTART && $PROCESS_COUNT -gt 0 ]]; then
    print_info "Agent is running and --force not specified. Exiting."
    exit 0
fi

# 8. Restart the agent
echo ""
print_info "Restarting sync agent..."

# Stop existing processes
if [[ $PROCESS_COUNT -gt 0 ]]; then
    print_info "Stopping existing sync agent processes..."
    pkill -f "timesheet_mirror_sync" || true
    sleep 2
    
    # Force kill if still running
    if pgrep -f "timesheet_mirror_sync" > /dev/null; then
        print_warning "Processes still running, forcing kill..."
        pkill -9 -f "timesheet_mirror_sync" || true
        sleep 1
    fi
    
    print_success "Existing processes stopped"
fi

# Restart via systemd or manually
if [[ "$SERVICE_STATUS" == "active" ]] || [[ "$SERVICE_STATUS" == "inactive" ]]; then
    print_info "Restarting via systemd..."
    $SUDO systemctl restart "$SERVICE_NAME"
    sleep 3
    
    # Verify service started
    if $SUDO systemctl is-active --quiet "$SERVICE_NAME"; then
        print_success "Service started successfully"
        
        # Show recent logs
        print_info "Recent logs:"
        $SUDO journalctl -u "$SERVICE_NAME" -n 5 --no-pager 2>/dev/null | tail -n 5 | sed 's/^/   /'
    else
        print_error "Service failed to start"
        print_info "Check logs: sudo journalctl -u $SERVICE_NAME -n 50"
        exit 1
    fi
else
    # Manual start (no systemd service)
    print_info "Starting manually..."
    print_info "   Command: $PYTHON_BIN $AGENT_PATH --hours $SYNC_HOURS"
    
    # Start in background
    nohup $PYTHON_BIN "$AGENT_PATH" --hours "$SYNC_HOURS" > /tmp/radai_sync_agent.log 2>&1 &
    PROCESS_PID=$!
    sleep 3
    
    # Verify process started
    if ps -p $PROCESS_PID > /dev/null; then
        print_success "Process started (PID: $PROCESS_PID)"
        print_info "   Logs: /tmp/radai_sync_agent.log"
    else
        print_error "Process failed to start or exited immediately"
        print_info "Check logs: cat /tmp/radai_sync_agent.log"
        exit 1
    fi
fi

echo ""
print_success "Sync agent restart initiated"
print_info "Monitor progress:"
echo "   1. Check Railway logs: https://railway.app"
echo "   2. Check frontend: https://www.radai.ae/hr/employees"
echo "   3. Look for 'POST /api/v1/timesheet/mirror/ingest/ 200' in logs"
echo ""
print_success "Data should update within 15-30 minutes"

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                     ✅ RESTART COMPLETE                       ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

exit 0
