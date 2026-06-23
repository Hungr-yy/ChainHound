"""The investigation canvas is served as static assets by the app."""

from fastapi.testclient import TestClient

from chainhound_server.app import create_app


def test_index_served_at_root():
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "ChainHound" in resp.text
    assert "cytoscape" in resp.text.lower()


def test_static_assets_served():
    client = TestClient(create_app())
    for path, needle in (
        ("/static/app.js", "cytoscape"),
        ("/static/styles.css", "#cy"),
    ):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert needle in resp.text


def test_api_routes_not_shadowed_by_static():
    # The /static mount and "/" route must not intercept API paths.
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok"}
