from fastapi.testclient import TestClient


def test_app_imports():
    from app.main import app

    assert app is not None
    client = TestClient(app)
    # legacy UI login page or redirect
    response = client.get("/login", follow_redirects=False)
    assert response.status_code in (200, 303, 307)
