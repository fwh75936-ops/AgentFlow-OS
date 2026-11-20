USERS = {1: {"id": 1, "name": "Alice", "role": "admin"}, 2: {"id": 2, "name": "Bob", "role": "user"}}
def get_profile(user_id):
    return USERS[user_id]
def admin_stats(requesting_user):
    if requesting_user.get("role") != "admin":
        raise PermissionError("admin only")
    return {"total_users": len(USERS)}
