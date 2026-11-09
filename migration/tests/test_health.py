def test_health_endpoints(client):
    assert client.get("/api/v1/health/live").json() == {"status": "ok"}
    assert client.get("/api/v1/health/ready").json() == {"status": "ready"}


def test_frontend_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "SlideSheet AI" in response.text
    assert client.get("/assets/app.css").status_code == 200
    assert client.get("/assets/app.js").status_code == 200


def test_readiness_skips_redis_check_in_sync_mode(client, monkeypatch):
    import app.api.routes.health as health_module

    settings = health_module.get_settings()
    assert settings.ingestion_mode == "sync"

    def _fail_if_called(_url):
        raise AssertionError("Redis should not be checked in sync ingestion mode")

    monkeypatch.setattr(health_module, "_check_redis", _fail_if_called)
    assert client.get("/api/v1/health/ready").json() == {"status": "ready"}


def test_readiness_checks_redis_in_celery_mode(client, monkeypatch):
    import app.api.routes.health as health_module

    settings = health_module.get_settings()
    monkeypatch.setattr(settings, "ingestion_mode", "celery")
    monkeypatch.setattr(health_module, "_check_redis", lambda url: None)
    assert client.get("/api/v1/health/ready").json() == {"status": "ready"}


def test_readiness_returns_503_when_redis_unreachable_in_celery_mode(client, monkeypatch):
    import app.api.routes.health as health_module

    settings = health_module.get_settings()
    monkeypatch.setattr(settings, "ingestion_mode", "celery")

    def _boom(_url):
        raise health_module.redis.RedisError("connection refused")

    monkeypatch.setattr(health_module, "_check_redis", _boom)
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
