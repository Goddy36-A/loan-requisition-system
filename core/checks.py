import sys
from django.conf import settings
from django.core.checks import Error, Tags, register
from django.db import OperationalError, ProgrammingError
from . import security


@register(Tags.security)
def sample_passwords_in_use(app_configs, **kwargs):
    """Stops runserver / migrate / check when the app is hosted but sample accounts still have their published passwords."""
    if settings.LOCAL_MODE: return []
    if sys.argv[1:2] == ["migrate"]: return []  # let a database from an older version catch up first; the guard applies to the next command
    try: names = security.accounts_with_published_passwords()
    except (OperationalError, ProgrammingError): return []
    return [Error(security.problem_message(names), id="core.E001")] if names else []
