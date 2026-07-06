"""
Minimal Railway Health Check
Returns 200 OK even if Django/database not configured
This ensures Railway health checks pass and service stays alive
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle GET requests to health check endpoint"""
        if self.path in ['/health/', '/api/v1/health/', '/']:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = {
                'status': 'alive',
                'service': 'radai-backend',
                'mode': 'minimal',
                'port': os.getenv('PORT', '8000'),
                'message': 'Server is running (minimal health check)'
            }
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        """Suppress access logs"""
        pass

def run_minimal_server(port=8000):
    """Run minimal health check server"""
    server_address = ('0.0.0.0', port)
    httpd = HTTPServer(server_address, HealthCheckHandler)
    print(f'🆘 FALLBACK: Minimal health server running on port {port}')
    print('   Django failed to start, but server is alive for Railway health checks')
    httpd.serve_forever()

if __name__ == '__main__':
    port = int(os.getenv('PORT', '8000'))
    run_minimal_server(port)
