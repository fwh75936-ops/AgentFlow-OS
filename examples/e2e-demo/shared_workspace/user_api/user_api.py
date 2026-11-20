"""Tiny user-management API core (demo project, stdlib only)."""
USERS = {
    1: {"id": 1, "name": "Alice", "role": "admin"},
    2: {"id": 2, "name": "Bob", "role": "user"},
}


def get_profile(user_id):
    return USERS[user_id]


def admin_stats(requesting_user):
    # FIX: only admins may read the admin statistics.
    if requesting_user.get("role") != "admin":
        raise PermissionError("admin role required")
    return {"total_users": len(USERS),
            "admins": sum(1 for u in USERS.values() if u["role"] == "admin")}
