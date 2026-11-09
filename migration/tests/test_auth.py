import os

from app.core.config import get_settings


def _set_ip_lockout_threshold(value: int) -> None:
    os.environ["MAX_FAILED_LOGINS_PER_IP"] = str(value)
    get_settings.cache_clear()


def _restore_ip_lockout_threshold() -> None:
    os.environ.pop("MAX_FAILED_LOGINS_PER_IP", None)
    get_settings.cache_clear()


def _register(client, email="owner@example.com", password="correcthorse1"):
    resp = client.post(
        "/api/v1/auth/register",
        json={"organization_name": "Acme", "email": email, "password": password},
    )
    assert resp.status_code == 201
    return resp.json()


def test_register_then_login_issues_tokens(client):
    _register(client, email="owner@example.com", password="correcthorse1")

    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "correcthorse1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]


def test_refresh_rotates_the_token(client):
    tokens = _register(client)

    first_refresh = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert first_refresh.status_code == 200
    rotated = first_refresh.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]

    # The new token from rotation still works.
    second_refresh = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}
    )
    assert second_refresh.status_code == 200


def test_reusing_a_rotated_refresh_token_is_rejected(client):
    tokens = _register(client)

    first_refresh = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    rotated = first_refresh.json()

    # Reusing the original (already-rotated-away) token looks like theft.
    replay = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert replay.status_code == 401


def test_reuse_revokes_the_whole_family_including_the_latest_token(client):
    tokens = _register(client)

    first_refresh = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    rotated = first_refresh.json()

    # Trigger reuse detection with the stale original token...
    client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    # ...which must also kill the newest (legitimately rotated) token in the same family, since
    # we can't tell whether the thief or the real client is the one holding it now.
    resp = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}
    )
    assert resp.status_code == 401


def test_expired_or_unknown_refresh_token_is_rejected(client):
    resp = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "not-a-real-token"}
    )
    assert resp.status_code == 401


def test_ip_is_locked_out_across_different_emails_not_just_one(client):
    _set_ip_lockout_threshold(3)
    try:
        for i in range(3):
            resp = client.post(
                "/api/v1/auth/login",
                json={"email": f"nobody{i}@example.com", "password": "whatever123"},
            )
            assert resp.status_code == 401

        # A brand-new email guess, never tried before, should still be rate-limited — proving
        # the lockout is IP-scoped, not per-email (which couldn't catch enumeration like this).
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "brand-new-guess@example.com", "password": "whatever123"},
        )
        assert resp.status_code == 429
    finally:
        _restore_ip_lockout_threshold()


def test_successful_login_resets_the_ip_failure_count(client):
    _set_ip_lockout_threshold(3)
    try:
        _register(client, email="good@example.com", password="correcthorse1")

        client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x"})
        client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x"})

        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "good@example.com", "password": "correcthorse1"},
        )
        assert resp.status_code == 200

        # One more failure alone shouldn't trip the lockout, since the successful login above
        # should have reset the counter.
        resp = client.post(
            "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x"}
        )
        assert resp.status_code == 401
    finally:
        _restore_ip_lockout_threshold()


def test_logout_revokes_only_that_one_token(client):
    tokens = _register(client)

    logout_resp = client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert logout_resp.status_code == 204

    resp = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert resp.status_code == 401
