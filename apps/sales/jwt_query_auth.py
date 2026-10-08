"""JWT authentication that also accepts the access token as a query parameter.

The letter PDF preview renders in an <iframe>, which cannot send the
``Authorization`` header. The preview URL therefore carries ``?token=<JWT>``;
this class validates it exactly like the header token. Header authentication
keeps working unchanged and takes precedence.
"""

from rest_framework_simplejwt.authentication import JWTAuthentication


class QueryParamJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            return result
        raw_token = request.query_params.get('token') or request.query_params.get('access_token')
        if not raw_token:
            return None
        validated_token = self.get_validated_token(raw_token)
        return self.get_user(validated_token), validated_token
