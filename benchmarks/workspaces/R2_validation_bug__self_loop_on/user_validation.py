def create_user(username, email):
    if not username or not str(username).strip():
        raise ValueError("username must not be empty")
    if not email or "@" not in str(email):
        raise ValueError("email is invalid")
    return {"username": username, "email": email}
