from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def client():
    """Create a test client for the FastAPI application."""
    from app.main import app

    return TestClient(app)
