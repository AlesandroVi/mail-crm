"""Ficha de contacto con notas, y limpieza de mails viejos (hallazgo SEC-009)."""
from conftest import agregar_mail, ultima_decision
from mail_crm import config, db


def test_ficha_de_contacto_y_notas(conn, cliente):
    agregar_mail(conn, "m1", asunto="Primero")
    agregar_mail(conn, "m2", asunto="Segundo", fecha="2099-02-01T10:00:00+00:00")
    contactos = cliente.get("/api/contactos").json()["contactos"]
    assert len(contactos) == 1 and contactos[0]["tiene_notas"] == 0
    cid = contactos[0]["id"]

    ficha = cliente.get(f"/api/contactos/{cid}").json()
    assert ficha["contacto"]["email_count"] == 2
    assert [m["subject"] for m in ficha["mails"]] == ["Segundo", "Primero"]

    assert cliente.post(f"/api/contactos/{cid}/notas", json={"notas": "  Reclutador  "}).status_code == 200
    assert cliente.get(f"/api/contactos/{cid}").json()["contacto"]["notes"] == "Reclutador"
    assert cliente.get("/api/contactos").json()["contactos"][0]["tiene_notas"] == 1
    assert cliente.post(f"/api/contactos/{cid}/notas", json={"notas": "x" * 2001}).status_code == 422
    assert cliente.get("/api/contactos/999").status_code == 404
    assert cliente.post("/api/contactos/999/notas", json={"notas": "x"}).status_code == 404


def test_limpieza_borra_lo_viejo_y_conserva_contactos(conn):
    viejo = agregar_mail(conn, "viejo", fecha="2001-01-01T00:00:00+00:00")
    nuevo = agregar_mail(conn, "nuevo", cuerpo="y" * 9000)
    db.guardar_decision(conn, viejo, "spam", "papelera", "IA", "gemini")
    db.guardar_borrador(conn, viejo, "texto")
    contacto = conn.execute("SELECT id FROM contacts").fetchone()["id"]
    db.guardar_notas(conn, contacto, "nota")

    assert db.limpiar_viejos(conn, 60, config.MAX_CUERPO) == 1
    assert conn.execute("SELECT 1 FROM emails WHERE id = ?", (viejo,)).fetchone() is None
    assert ultima_decision(conn, viejo) is None
    assert conn.execute("SELECT COUNT(*) AS n FROM drafts").fetchone()["n"] == 0
    assert len(conn.execute("SELECT body FROM emails WHERE id = ?", (nuevo,)).fetchone()["body"]) == config.MAX_CUERPO
    assert db.contacto_por_id(conn, contacto)["notes"] == "nota"


def test_se_guarda_solo_lo_que_lee_la_ia():
    assert config.MAX_CUERPO == max(config.CUERPO_PARA_IA, config.CUERPO_PARA_BORRADOR)


def test_base_vieja_recibe_las_columnas_nuevas(tmp_path):
    import sqlite3
    ruta = tmp_path / "vieja.db"
    vieja = sqlite3.connect(ruta)
    vieja.executescript("CREATE TABLE contacts (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT, "
                        "first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, email_count INTEGER NOT NULL DEFAULT 0);")
    vieja.close()
    conn = db.conectar(ruta)
    columnas = {f["name"] for f in conn.execute("PRAGMA table_info(contacts)")}
    conn.close()
    assert "notes" in columnas
