def can_access(user, resource):
    """BUG: any logged-in user can access every resource, incl. admin_panel."""
    if user is None:
        return False
    return True
