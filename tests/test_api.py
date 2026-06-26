import pytest
from fastapi.testclient import TestClient


API_KEY = "test-api-key-123"
API_HEADERS = {"X-API-Key": API_KEY}


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


class TestHealth:
    def test_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data
        assert "help" in data["message"]

    def test_ping_ok(self, client):
        resp = client.get("/ping")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("app") == "ok"

    def test_openapi_available(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200


class TestRateLimit:
    def test_rate_limit_exempts_ping(self, client):
        for _ in range(20):
            resp = client.get("/ping")
            assert resp.status_code == 200

    def test_rate_limit_exempts_root(self, client):
        for _ in range(20):
            resp = client.get("/")
            assert resp.status_code == 200


class TestCRUD:
    def test_create_opportunity(self, client):
        resp = client.post("/opportunities", json={
            "title": "Test Scholarship",
            "link": "https://example.com/test-scholarship",
        }, headers=API_HEADERS)
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Test Scholarship"
        assert data["link"] == "https://example.com/test-scholarship"
        assert "id" in data

    def test_create_duplicate_rejected(self, client):
        resp = client.post("/opportunities", json={
            "title": "Test Scholarship",
            "link": "https://example.com/test-scholarship",
        }, headers=API_HEADERS)
        assert resp.status_code == 400

    def test_get_opportunity(self, client):
        resp = client.get("/opportunities/1")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Test Scholarship"

    def test_get_nonexistent(self, client):
        resp = client.get("/opportunities/99999")
        assert resp.status_code == 404

    def test_list_opportunities(self, client):
        resp = client.get("/opportunities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert len(data["results"]) >= 1

    def test_update_opportunity(self, client):
        resp = client.put("/opportunities/1", json={"title": "Updated Title"}, headers=API_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated Title"

    def test_update_nonexistent(self, client):
        resp = client.put("/opportunities/99999", json={"title": "Nope"}, headers=API_HEADERS)
        assert resp.status_code == 404

    def test_unposted_list(self, client):
        resp = client.get("/opportunities/unposted")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_posted_list(self, client):
        resp = client.get("/opportunities/posted")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_stats(self, client):
        resp = client.get("/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "unposted" in data
        assert "posted" in data

    def test_delete_opportunity(self, client):
        resp = client.delete("/opportunities/1", headers=API_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        resp = client.get("/opportunities/1")
        assert resp.status_code == 404

    def test_delete_nonexistent(self, client):
        resp = client.delete("/opportunities/99999", headers=API_HEADERS)
        assert resp.status_code == 404

    def test_auth_required_for_write(self, client):
        resp = client.post("/opportunities", json={"title": "x", "link": "https://x.com"})
        assert resp.status_code == 403

        resp = client.delete("/opportunities/1")
        assert resp.status_code == 403

        resp = client.put("/opportunities/1", json={"title": "x"})
        assert resp.status_code == 403

    def test_bulk_delete(self, client):
        ids = []
        for i in range(3):
            resp = client.post("/opportunities", json={
                "title": f"Bulk {i}", "link": f"https://example.com/bulk-{i}"
            }, headers=API_HEADERS)
            assert resp.status_code == 201
            ids.append(str(resp.json()["id"]))
        resp = client.delete(f"/opportunities?ids={','.join(ids)}", headers=API_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 3
