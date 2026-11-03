import base64
import uuid

from conftest import make_user, op, push

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


def facture(c):
    pid = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment", "price": 100_000}))[0]["product"]["id"]
    cid = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Camara"}))[0]["client"]["id"]
    return push(c, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 1}]}))[0]["sale"]


def test_logo_sur_les_documents(owner):
    s = facture(owner)
    assert owner.get("/api/logo").status_code == 404 and 'src="/api/logo"' not in owner.get(f"/api/invoice/{s['id']}").text
    assert owner.post("/api/logo", json={"image": PNG}).status_code == 200
    r = owner.get("/api/logo")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    assert 'src="/api/logo"' in owner.get(f"/api/invoice/{s['id']}").text
    assert owner.delete("/api/logo").status_code == 200 and owner.get("/api/logo").status_code == 404
    acts = [e["action"] for e in owner.get("/api/audit?limit=20").json()["entries"]]
    assert "logo.updated" in acts and "logo.removed" in acts


def test_logo_refuse_ce_qui_n_est_pas_une_image_sure(owner, app_env):
    svg = base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>").decode()
    assert owner.post("/api/logo", json={"image": svg}).status_code == 422                       # SVG refusé (script)
    assert owner.post("/api/logo", json={"image": "pas du base64 !!"}).status_code == 422
    big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 260_000).decode()
    assert owner.post("/api/logo", json={"image": big}).status_code in (413, 422)
    marie, _ = make_user(owner, app_env, "marie", "sales")
    assert marie.post("/api/logo", json={"image": PNG}).status_code == 403 and marie.delete("/api/logo").status_code == 403
