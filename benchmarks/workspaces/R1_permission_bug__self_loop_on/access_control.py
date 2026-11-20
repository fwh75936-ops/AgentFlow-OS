def can_access(user, resource):
    if user is None:
        return False
    if resource == "admin_panel":
        return user.get("role") == "admin"
    return True
