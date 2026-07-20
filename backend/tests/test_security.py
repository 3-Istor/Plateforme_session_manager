import pytest
from fastapi import HTTPException

from backend.app.auth import current_user, manager_only
from backend.app.config import Settings


def test_demo_role_is_derived_from_server_configuration():
    settings = Settings(
        auth_mode="demo",
        manager_email="lead@3istor.fr",
        team_members="lead@3istor.fr,member@3istor.fr",
    )
    manager = current_user(authorization=None, x_demo_user="lead@3istor.fr", settings=settings)
    member = current_user(authorization=None, x_demo_user="member@3istor.fr", settings=settings)
    assert manager.is_manager is True
    assert member.is_manager is False


def test_unknown_demo_user_is_rejected():
    settings = Settings(
        auth_mode="demo",
        manager_email="lead@3istor.fr",
        team_members="lead@3istor.fr,member@3istor.fr",
    )
    with pytest.raises(HTTPException) as error:
        current_user(authorization=None, x_demo_user="intruder@example.com", settings=settings)
    assert error.value.status_code == 403


def test_manager_guard_cannot_be_bypassed_by_a_member():
    settings = Settings(
        auth_mode="demo",
        manager_email="lead@3istor.fr",
        team_members="lead@3istor.fr,member@3istor.fr",
    )
    member = current_user(authorization=None, x_demo_user="member@3istor.fr", settings=settings)
    with pytest.raises(HTTPException) as error:
        manager_only(member)
    assert error.value.status_code == 403
