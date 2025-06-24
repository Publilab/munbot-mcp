import os
import pytest
os.environ["POSTGRES_PORT"] = "5432"
os.environ["TESTING"] = "1"
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200

def test_list_available():
    response = client.get("/appointments/available")
    assert response.status_code == 200
    assert "disponibles" in response.json()
