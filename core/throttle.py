"""Sign-in throttling: lock a username after repeated wrong passwords. Stored in the database so it survives restarts and
works across several server processes. Wrong attempts for usernames that do not exist are counted the same way, so the
response never reveals which usernames are real."""
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import AuditLog, LoginAttempt


def client_ip(request):
    if settings.BEHIND_PROXY:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded: return forwarded.split(",")[-1].strip()[:45]  # the address our own proxy saw (earlier entries can be forged)
    return (request.META.get("REMOTE_ADDR") or "unknown")[:45]  # 45 = longest IPv6 text form; keeps keys inside the column


def _keys(username, ip):
    u = (username or "").strip().lower()[:150]
    return [(f"user-ip:{u}|{ip}", settings.LOGIN_MAX_FAILURES), (f"user:{u}", settings.LOGIN_USER_MAX_FAILURES)]


def lock_remaining(username, ip):
    """Seconds until this username may try again (0 = not locked). Expired locks are cleared."""
    now, longest = timezone.now(), 0
    for key, _ in _keys(username, ip):
        row = LoginAttempt.objects.filter(key=key).first()
        if row and row.locked_until:
            if row.locked_until > now: longest = max(longest, int((row.locked_until - now).total_seconds()) + 1)
            else: row.delete()  # the lock has been served: start counting afresh
    return longest


@transaction.atomic
def record_failure(username, ip):
    now, window = timezone.now(), timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
    for key, limit in _keys(username, ip):
        row, _ = LoginAttempt.objects.select_for_update().get_or_create(key=key, defaults={"first_failure_at": now})
        if not row.locked_until and now - row.first_failure_at > window:  # old failures expire
            row.failures, row.first_failure_at = 0, now
        row.failures += 1
        if row.failures >= limit and not row.locked_until:
            row.locked_until = now + window
            AuditLog.objects.create(user=None, action="Sign-in locked", description=f"{key} after {row.failures} failed attempts "
                                    f"(locked for {settings.LOGIN_LOCK_MINUTES} minute(s))"[:500])
        row.save()


def record_success(username, ip):
    LoginAttempt.objects.filter(key__in=[k for k, _ in _keys(username, ip)]).delete()


def unlock(username=None):
    """Clear lockouts for one username (or everyone). Returns how many counters were removed."""
    qs = LoginAttempt.objects.all()
    if username:
        u = username.strip().lower()
        from django.db.models import Q
        qs = qs.filter(Q(key=f"user:{u}") | Q(key__startswith=f"user-ip:{u}|"))
    return qs.delete()[0]
