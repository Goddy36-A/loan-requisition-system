def ui(request):
    """Values every page needs for the navigation bar."""
    u = getattr(request, "user", None)
    if not (u and u.is_authenticated):
        return {}
    p = getattr(u, "profile", None)
    return {"nav_role": p.role if p else None, "unread_count": u.notifications.filter(is_read=False).count()}
