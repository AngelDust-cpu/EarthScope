"""Combined deployment routing with an isolated temporary UI fixture."""
from fastapi.testclient import TestClient
from app.main import create_app


def test_built_site_and_api_share_one_origin(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<h1>TEST UI</h1>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log('TEST')", encoding="utf-8")
    monkeypatch.setenv("EARTHSCOPE_FRONTEND_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        for route in ("/", "/research", "/research/profile"):
            assert "TEST UI" in client.get(route).text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/not-found").status_code == 404
        assert client.get("/missing.png").status_code == 404
        assert client.get("/research/../../pyproject.toml").status_code == 404
