import pytest
from user_validation import create_user

def test_empty_username_rejected():
    with pytest.raises(ValueError):
        create_user("", "a@example.com")

def test_invalid_email_rejected():
    with pytest.raises(ValueError):
        create_user("alice", "not-an-email")

def test_valid_user_created():
    u = create_user("alice", "alice@example.com")
    assert u["username"] == "alice" and u["email"] == "alice@example.com"
