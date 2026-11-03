"""Éléments communs aux tests navigateur : serveur jetable, navigateur, page avec suivi des erreurs JavaScript."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

RACINE = Path(__file__).resolve().parent.parent
H = {"X-Requested-With": "gestion", "X-Device-Id": "test-navigateur"}
PW = "Motdepasse2026ok"


def _port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope="module")
def serveur(tmp_path_factory):
    d = tmp_path_factory.mktemp("donnees_nav"); port = _port()
    env = {**os.environ, "GESTION_DATA_DIR": str(d), "GESTION_CHIFFREMENT": "0", "GESTION_NO_AUTOBACKUP": "1", "PYTHONIOENCODING": "utf-8"}
    p = subprocess.Popen([sys.executable, "-m", "gestion.main", "--port", str(port)], cwd=RACINE, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(url + "/", timeout=1); break
        except OSError:
            time.sleep(0.5)
    else:
        p.kill(); pytest.fail("serveur de test injoignable")
    yield url
    p.terminate()
    try: p.wait(10)
    except subprocess.TimeoutExpired: p.kill()


@pytest.fixture(scope="module")
def navigateur():
    with playwright_api.sync_playwright() as pw:
        b = None
        for kw in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
            try:
                b = pw.chromium.launch(**kw); break
            except Exception:
                continue
        if b is None: pytest.skip("aucun navigateur Chromium/Edge/Chrome disponible")
        yield b
        b.close()


class Page:
    """Une page ouverte sur l'application, avec la liste des erreurs JavaScript rencontrées."""

    def __init__(self, navigateur, url, langue="fr-FR"):
        self.ctx = navigateur.new_context(locale=langue, base_url=url, extra_http_headers={"Accept-Language": langue}, service_workers="block")
        self.page = self.ctx.new_page(); self.erreurs: list[str] = []
        self.page.set_default_timeout(8000)                                   # un test qui attend 30 s par clic est inutilisable
        self.page.on("pageerror", lambda e: self.erreurs.append(str(e)))
        self.page.on("console", lambda m: self.erreurs.append(m.text) if m.type == "error" and "favicon" not in m.text else None)

    def api(self, methode, chemin, **kw):
        r = getattr(self.ctx.request, methode)(chemin, headers=H, **kw)
        assert r.ok, f"{chemin} : {r.status} {r.text()}"
        return r.json()

    def push(self, kind, data):
        r = self.api("post", "/api/sync/push", data={"ops": [{"op_id": str(uuid.uuid4()), "type": kind, "data": data}]})["results"][0]
        assert r["status"] == "ok", r
        return r

    def close(self): self.ctx.close()


def aller(page, nom: str):
    """Ouvre un onglet par son nom français : ouvre d'abord sa rubrique si l'onglet n'est pas affiché."""
    t = page.get_by_role("tab", name=nom, exact=True)
    if not (t.count() and t.first.is_visible()):
        page.locator(f'[data-noms*="|{nom}|"]').first.click(); page.wait_for_timeout(200)
        menu = page.locator(".tabs .sous-select")
        if menu.count():
            menu.select_option(label=nom)
            return
        t = page.get_by_role("tab", name=nom, exact=True)
    if t.count(): t.first.click()
    page.wait_for_timeout(300)
