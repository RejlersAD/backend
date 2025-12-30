"""
Test simple_health.py WSGI wrapper locally
"""

def test_health_check():
    """Test the health check endpoint"""
    from simple_health import simple_health_check
    
    # Mock WSGI environ for health check
    class MockStartResponse:
        def __init__(self):
            self.status = None
            self.headers = None
        
        def __call__(self, status, headers):
            self.status = status
            self.headers = headers
    
    # Test /api/v1/health/
    environ = {'PATH_INFO': '/api/v1/health/'}
    start_response = MockStartResponse()
    
    result = simple_health_check(environ, start_response)
    
    assert result is not None, "Health check should return response"
    assert start_response.status == '200 OK', f"Expected 200 OK, got {start_response.status}"
    assert b'healthy' in result[0], "Response should contain 'healthy'"
    
    print("✅ /api/v1/health/ test PASSED")
    
    # Test /health/
    environ = {'PATH_INFO': '/health/'}
    start_response = MockStartResponse()
    
    result = simple_health_check(environ, start_response)
    
    assert result is not None, "Health check should return response"
    assert start_response.status == '200 OK', f"Expected 200 OK, got {start_response.status}"
    
    print("✅ /health/ test PASSED")
    
    # Test other path (should return None to pass to Django)
    environ = {'PATH_INFO': '/api/v1/users/'}
    start_response = MockStartResponse()
    
    result = simple_health_check(environ, start_response)
    
    assert result is None, "Non-health paths should return None"
    
    print("✅ Other path test PASSED (correctly returns None)")
    
    print("\n🎉 All tests passed! simple_health.py is working correctly")

if __name__ == '__main__':
    test_health_check()
