"""
Custom Pagination Classes for RBAC API
Allows flexible page sizes for better frontend control
"""
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class FlexiblePageNumberPagination(PageNumberPagination):
    """
    Flexible pagination that allows frontend to control page size.
    
    Usage:
    - Default: ?page=1 (returns 10 items)
    - Custom page size: ?page=1&page_size=25
    - Get all results: ?page_size=1000 (or large number)
    
    Max page size is limited to prevent performance issues.
    """
    page_size = 10  # Default page size
    page_size_query_param = 'page_size'  # Allow frontend to specify page_size
    max_page_size = 1000  # Maximum items per page (safety limit)
    page_query_param = 'page'
    
    def get_paginated_response(self, data):
        """
        Return paginated response with metadata
        """
        return Response({
            'count': self.page.paginator.count,
            'next': self.get_next_link(),
            'previous': self.get_previous_link(),
            'total_pages': self.page.paginator.num_pages,
            'current_page': self.page.number,
            'page_size': self.page_size,
            'results': data
        })
