import pytest

from app.models import UserProfile
from app.services.cookie import dump_profile


def test_home_renders_shell(client, signed_cookie_for, empty_profile):
    client.cookies.set("session", signed_cookie_for(empty_profile))
    response = client.get("/")
    assert response.status_code == 200
    # Feed grid is empty — articles are populated client-side via IndexedDB + feed.js
    assert 'id="feed"' in response.text
    # Server injects profile init data for JS
    assert '__RH_INIT__' in response.text


def test_home_returning_user_renders_shell(client, signed_cookie_for, returning_profile):
    client.cookies.set("session", signed_cookie_for(returning_profile))
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="feed"' in response.text


def test_home_no_categories_redirects(client):
    client.cookies.set("session", dump_profile(UserProfile(timezone="UTC", selected_categories=[])))
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/onboarding"


def test_home_custom_interests_only_does_not_redirect(client):
    profile = UserProfile(
        timezone="UTC",
        selected_categories=[],
        custom_interests=["Byzantine architecture"],
    )
    client.cookies.set("session", dump_profile(profile))
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200


def test_home_no_cookie_redirects(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/onboarding"
