"""Default non-interactive test runner using Django's reusable test database."""

from django.test.runner import DiscoverRunner


class NonInteractiveKeepDbRunner(DiscoverRunner):
    """Avoid database-exists prompts and reuse the isolated test database."""

    def __init__(self, *args, **kwargs):
        kwargs['interactive'] = False
        kwargs['keepdb'] = True
        super().__init__(*args, **kwargs)
