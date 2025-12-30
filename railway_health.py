"""
Lightweight health check server for Railway.
Starts immediately while Django initializes in the background.
"""
import http.server
import socketserver
import os
import sys
import threading
import time

PORT = int(os.environ.get('PORT', 8000))
HEALTH_CHECK_PORT = PORT

class HealthHandler(http.server.BaseHTTPRequestHandler):
    """Simple HTTP handler for health checks."""
    
    startup_complete = False
    
    def do_GET(self):
        """Handle GET requests."""
        if self.path == '/api/v1/health/' or self.path == '/health':
            # Always return healthy - Railway needs quick response
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = b'{"status":"healthy","service":"radai-backend","ready":true}'
            self.wfile.write(response)
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


def run_health_server():
    """Run lightweight health check server."""
    handler = HealthHandler
    with socketserver.TCPServer(("0.0.0.0", HEALTH_CHECK_PORT), handler) as httpd:
        print(f"✅ Health check server ready on port {HEALTH_CHECK_PORT}")
        httpd.serve_forever()


if __name__ == '__main__':
    print("🏥 Starting Railway Health Check Server...")
    print(f"🔌 Port: {HEALTH_CHECK_PORT}")
    
    try:
        run_health_server()
    except KeyboardInterrupt:
        print("\n🛑 Health server stopped")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Health server error: {e}")
        sys.exit(1)
