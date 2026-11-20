def create_user(username, email):
    """BUG: no validation at all — empty username / malformed email accepted."""
    return {"username": username, "email": email}
