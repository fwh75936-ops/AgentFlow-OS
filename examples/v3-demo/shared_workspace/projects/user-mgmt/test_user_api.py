import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from user_api import get_profile, admin_stats
def test_profile():
    assert get_profile(1)["name"] == "Alice"
def test_admin_stats_rejects_non_admin():
    try:
        admin_stats({"id": 2, "role": "user"})
    except PermissionError:
        return
    raise AssertionError("non-admin must be rejected")
