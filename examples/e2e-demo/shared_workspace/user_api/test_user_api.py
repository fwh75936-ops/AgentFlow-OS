import pytest

from user_api import USERS, admin_stats, get_profile


def test_regular_user_cannot_access_admin_stats():
    with pytest.raises(PermissionError):
        admin_stats(USERS[2])  # Bob is a regular user


def test_admin_can_access_admin_stats():
    stats = admin_stats(USERS[1])  # Alice is an admin
    assert stats["total_users"] == 2
    assert stats["admins"] == 1


def test_get_profile_returns_user():
    assert get_profile(2)["name"] == "Bob"
