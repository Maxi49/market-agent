import pytest
from fastapi.testclient import TestClient
import httpx

from app.main import app

client = TestClient(app)

@pytest.fixture
def mock_serpapi_env(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "test_key")

def test_search_everywhere_success(mock_serpapi_env, respx_mock):
    mock_response = {
        "shopping_results": [
            {"title": "Test Product", "price": "$100", "link": "http://test.com/product"}
        ]
    }
    
    respx_mock.get("https://serpapi.com/search.json").mock(return_value=httpx.Response(200, json=mock_response))

    response = client.get("/agent/search-everywhere?query=iphone&url=test.com")
    
    assert response.status_code == 200
    data = response.json()
    assert "shopping_results" in data
    assert len(data["shopping_results"]) == 1
    assert data["shopping_results"][0]["title"] == "Test Product"

def test_search_everywhere_missing_api_key(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("SERP_API_KEY", raising=False)
    
    response = client.get("/agent/search-everywhere?query=iphone")
    
    assert response.status_code == 200
    data = response.json()
    assert data.get("error") == "SERPAPI_API_KEY/SERP_API_KEY no configurado."
    assert "shopping_results" in data
    assert len(data["shopping_results"]) == 0

def test_search_everywhere_serpapi_error(mock_serpapi_env, respx_mock):
    respx_mock.get("https://serpapi.com/search.json").mock(return_value=httpx.Response(500, text="Internal Server Error"))
    
    response = client.get("/agent/search-everywhere?query=iphone")
    
    assert response.status_code == 200
    data = response.json()
    assert "Error consultando SerpAPI" in data.get("error", "")
