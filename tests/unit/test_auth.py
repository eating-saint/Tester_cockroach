import pytest

pytest.importorskip("aiohttp")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from hands.config import Config  # noqa: E402
from panel.server import create_app  # noqa: E402
from panel.service import PanelService  # noqa: E402


@pytest.fixture
async def client(tmp_path):
    c = Config(chrome_path="x", runs_dir=tmp_path / "runs", profile_dir=tmp_path / "p")
    svc = PanelService(c)
    port_ref = {"port": 0}
    app = create_app(svc, "TOK", port_ref)
    server = TestServer(app, host="127.0.0.1")
    cl = TestClient(server)
    await cl.start_server()
    port_ref["port"] = server.port
    yield cl, server.port
    await cl.close()
    svc.preview.close()


async def test_token_origin_host(client):
    cl, port = client
    good = {"X-Panel-Token": "TOK"}
    assert (await cl.get("/api/samples/grid")).status == 403
    assert (await cl.get("/api/samples/grid", headers={"X-Panel-Token": "bad"})).status == 403
    assert (await cl.get("/api/samples/grid", headers={**good, "Origin": "http://evil.example"})).status == 403
    assert (await cl.get("/api/samples/grid", headers={**good, "Host": f"192.168.1.2:{port}"})).status == 403
    r = await cl.get("/api/samples/grid", headers={**good, "Origin": f"http://127.0.0.1:{port}"})
    assert r.status == 200 and (await r.json())["ok"]
    assert (await cl.get("/api/state", headers=good)).status == 200


async def test_index_injects_token_and_csp(client):
    cl, port = client
    r = await cl.get("/")
    t = await r.text()
    assert r.status == 200 and 'content="TOK"' in t and "{{TOKEN}}" not in t
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert (await cl.get("/", headers={"Host": "evil.example"})).status == 403


async def test_runs_traversal(client):
    cl, port = client
    r = await cl.get("/runs/..%2F..%2Fconfig.yaml", headers={"X-Panel-Token": "TOK"})
    assert r.status in (403, 404)
