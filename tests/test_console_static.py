from __future__ import annotations

from fastapi.testclient import TestClient

from agentbridge.api import create_app


def test_console_serves_spa_shell_and_assets():
    client = TestClient(create_app())

    page = client.get("/console")
    assert page.status_code == 200
    assert "AgentBridge 控制台" in page.text
    assert "/console/static/app.js" in page.text

    app_js = client.get("/console/static/app.js")
    assert app_js.status_code == 200
    assert "text/javascript" in app_js.headers["content-type"]
    assert "createApp" in app_js.text

    vue = client.get("/console/static/vendor/vue.esm-browser.prod.js")
    assert vue.status_code == 200

    styles = client.get("/console/static/styles.css")
    assert styles.status_code == 200
    assert "text/css" in styles.headers["content-type"]


def test_console_static_rejects_path_traversal():
    client = TestClient(create_app())
    assert client.get("/console/static/../../api.py").status_code == 404
