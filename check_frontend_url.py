from django.conf import settings
print("FRONTEND_URL:", getattr(settings, "FRONTEND_URL", "NOT SET"))
