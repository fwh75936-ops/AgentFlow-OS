from access_control import can_access

def test_anonymous_denied_everywhere():
    assert can_access(None, "profile") is False
    assert can_access(None, "admin_panel") is False

def test_regular_user_denied_admin_panel():
    assert can_access({"name": "u1", "role": "user"}, "admin_panel") is False

def test_admin_allowed_admin_panel():
    assert can_access({"name": "root", "role": "admin"}, "admin_panel") is True

def test_regular_user_allowed_profile():
    assert can_access({"name": "u1", "role": "user"}, "profile") is True
