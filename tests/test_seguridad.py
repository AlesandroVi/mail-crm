"""Login, sesiones, cabeceras y limites del panel (hallazgos SEC-002, 003, 004, 013 y 015)."""
import pytest

from conftest import CLAVE, HASH, nuevo_cliente
from mail_crm import auth, config, panel


def entrar(c, clave=CLAVE):
    return c.post("/login", json={"clave": clave})


def test_sin_sesion_no_se_ve_nada():
    c = nuevo_cliente()
    assert c.get("/api/mails").status_code == 401
    assert c.get("/").status_code == 303
    assert c.get("/salud").status_code == 200
    assert c.get("/sw.js").status_code == 200  # publico: no tiene datos


def test_cabeceras_de_seguridad_en_todas_las_respuestas():
    c = nuevo_cliente()
    respuestas = [c.get("/"), c.get("/api/mails"), c.get("/login"),
                  c.post("/api/aplicar", json={}, headers={"origin": "https://malo.example"})]
    assert [r.status_code for r in respuestas] == [303, 401, 200, 403]
    for r in respuestas:
        assert "default-src 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-content-type-options"] == "nosniff"
    assert "max-age" in c.get("/login", headers={"x-forwarded-proto": "https"}).headers["strict-transport-security"]
    assert "unsafe" not in panel.CSP and "worker-src 'self'" in panel.CSP


def test_las_paginas_no_tienen_estilos_ni_eventos_en_linea():
    for pagina in ("index.html", "login.html"):
        html = (panel.WEB / pagina).read_text(encoding="utf-8")
        assert " style=" not in html and " onclick=" not in html, pagina


def test_cerrar_sesion_invalida_todas_las_cookies():
    a, b = nuevo_cliente(), nuevo_cliente()
    assert entrar(a).status_code == 200 and entrar(b).status_code == 200
    copiada = a.cookies.get("sesion")
    assert a.post("/logout").status_code == 200
    ladron = nuevo_cliente()
    ladron.cookies.set("sesion", copiada)
    assert ladron.get("/api/mails").status_code == 401
    assert b.get("/api/mails").status_code == 401
    panel._version = None  # como si el servidor se reiniciara: la version sale de la base
    assert ladron.get("/api/mails").status_code == 401


def test_cambiar_la_clave_cierra_las_sesiones(cliente, monkeypatch):
    assert cliente.get("/api/mails").status_code == 200
    monkeypatch.setattr(config, "PANEL_CLAVE_HASH", auth.hash_clave(CLAVE + "-nueva"))
    assert cliente.get("/api/mails").status_code == 401


def test_un_extrano_no_bloquea_a_un_dispositivo_conocido():
    duenio = nuevo_cliente()
    assert entrar(duenio).status_code == 200
    extrano = nuevo_cliente()
    for i in range(30):
        extrano.headers["x-forwarded-for"] = f"10.0.0.{i}"  # cambiar la IP no ayuda
        assert entrar(extrano, "mala").status_code == 401
    assert entrar(extrano, "mala").status_code == 429
    assert entrar(nuevo_cliente()).status_code == 429  # los desconocidos comparten el limite
    assert entrar(duenio).status_code == 200
    falso = nuevo_cliente()
    falso.cookies.set("dispositivo", "abc.firma-falsa")
    assert entrar(falso).status_code == 429


def test_limites_de_tamano(cliente):
    assert entrar(nuevo_cliente(), "x" * 201).status_code == 422
    r = cliente.post("/api/reglas", content=b"x" * (panel.MAX_PEDIDO + 1),
                     headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_sin_clave_solo_responde_a_esta_pc(monkeypatch):
    monkeypatch.setattr(config, "PANEL_CLAVE_HASH", "")
    local = nuevo_cliente(base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    assert local.get("/api/mails").status_code == 200
    de_la_red = nuevo_cliente(base_url="http://127.0.0.1:8000", client=("192.168.0.20", 50000))
    assert de_la_red.get("/api/mails").status_code == 403
    rebinding = nuevo_cliente(base_url="http://sitio-malo.example", client=("127.0.0.1", 50000))
    assert rebinding.get("/api/mails").status_code == 403


def test_no_arranca_en_internet_sin_clave(monkeypatch):
    monkeypatch.setattr(config, "PANEL_CLAVE_HASH", "")
    monkeypatch.setattr(config, "PANEL_HOST", "0.0.0.0")
    with pytest.raises(RuntimeError):
        with nuevo_cliente():
            pass
    monkeypatch.setattr(config, "PANEL_CLAVE_HASH", HASH)
    with nuevo_cliente() as c:  # con clave arranca bien
        assert c.get("/salud").status_code == 200


def test_tarea_programada_exige_su_token(monkeypatch):
    c = nuevo_cliente()
    assert c.post("/api/tarea/actualizar").status_code == 404  # sin token configurado no existe
    monkeypatch.setattr(config, "TAREA_TOKEN", "token-de-prueba")
    assert c.post("/api/tarea/actualizar", headers={"authorization": "Bearer otro"}).status_code == 401
