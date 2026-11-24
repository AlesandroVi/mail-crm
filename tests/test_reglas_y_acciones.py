"""Reglas por remitente, aplicar y deshacer en Gmail, y reglas que se aplican solas."""
from conftest import GmailFalso, agregar_mail, ultima_decision
from mail_crm import acciones, avisos, clasificar, db, gmail_client, panel, resumen, sync


def test_buscar_regla_prefiere_el_mail_exacto_y_el_dominio_mas_largo(conn):
    db.guardar_regla(conn, "tienda.com", "archivar")
    db.guardar_regla(conn, "ofertas.tienda.com", "papelera")
    db.guardar_regla(conn, "jefe@tienda.com", "destacar")
    reglas = db.listar_reglas(conn)
    assert db.buscar_regla(reglas, "JEFE@tienda.com")["action"] == "destacar"
    assert db.buscar_regla(reglas, "x@ofertas.tienda.com")["action"] == "papelera"
    assert db.buscar_regla(reglas, "x@tienda.com")["action"] == "archivar"
    assert db.buscar_regla(reglas, "x@otratienda.com") is None  # no es un subdominio


def test_reaplicar_reglas(conn):
    mail = agregar_mail(conn)
    db.guardar_decision(conn, mail, "promocion", "archivar", "IA", "gemini")
    db.guardar_regla(conn, "tienda.com", "papelera")
    assert db.reaplicar_reglas(conn) == 1
    assert ultima_decision(conn, mail)["action"] == "papelera"
    db.borrar_regla(conn, "tienda.com")
    assert db.reaplicar_reglas(conn) == 1
    assert ultima_decision(conn, mail) is None  # vuelve a quedar pendiente para la IA


def test_aplicar_y_deshacer(conn):
    mail = agregar_mail(conn)
    db.guardar_decision(conn, mail, "spam", "papelera", "IA", "gemini")
    gmail = GmailFalso()
    assert acciones.aplicar_decisiones(conn, gmail)["aplicadas"] == 1
    decision = ultima_decision(conn, mail)
    assert decision["applied"] == 1 and gmail.hechos == [("papelera", "m1")]
    acciones.deshacer_decision(conn, gmail, decision["id"])
    decision = ultima_decision(conn, mail)
    assert decision["applied"] == 0 and decision["undone"] == 1
    assert gmail.hechos[-1] == ("sacar de papelera", "m1")


def test_las_reglas_automaticas_no_tocan_lo_de_la_ia_ni_lo_deshecho(conn):
    de_regla = agregar_mail(conn, "m1", de="promo@betx.com")
    de_ia = agregar_mail(conn, "m2", de="alguien@spam.com")
    deshecho = agregar_mail(conn, "m3", de="otra@betx.com")
    db.guardar_decision(conn, de_regla, "regla", "papelera", "Regla", "regla")
    db.guardar_decision(conn, de_ia, "spam", "papelera", "IA", "gemini")
    db.guardar_decision(conn, deshecho, "regla", "papelera", "Regla", "regla")
    db.marcar_aplicada(conn, ultima_decision(conn, deshecho)["id"], True)
    db.marcar_aplicada(conn, ultima_decision(conn, deshecho)["id"], False)  # lo deshiciste

    gmail = GmailFalso()
    assert acciones.aplicar_reglas(conn, gmail)["aplicadas"] == 1
    assert gmail.hechos == [("papelera", "m1")]
    assert ultima_decision(conn, de_ia)["applied"] == 0


def _actualizar_con(monkeypatch, conn, por_categoria):
    gmail = GmailFalso()
    enviados = []
    monkeypatch.setattr(sync, "sincronizar", lambda interactivo: {"nuevos": 2, "revisados": 2})
    monkeypatch.setattr(clasificar, "clasificar_pendientes",
                        lambda: {"clasificados": 2, "pendientes": 0, "aviso": None, "por_categoria": por_categoria})
    monkeypatch.setattr(gmail_client, "obtener_servicio", lambda interactivo=True: gmail)
    monkeypatch.setattr(avisos, "enviar", lambda c, titulo, texto: enviados.append(texto) or 1)
    monkeypatch.setattr(resumen, "toca_hoy", lambda c: False)
    return panel._actualizar(), gmail, enviados


def test_actualizar_aplica_reglas_limpia_y_avisa(conn, monkeypatch):
    mail = agregar_mail(conn, de="promo@betx.com")
    db.guardar_decision(conn, mail, "regla", "papelera", "Regla", "regla")
    viejo = agregar_mail(conn, "viejo", fecha="2001-01-01T00:00:00+00:00")
    r, gmail, enviados = _actualizar_con(monkeypatch, conn, {"requiere_respuesta": 2})
    assert r["aplicadas_por_reglas"] == 1 and ("papelera", "m1") in gmail.hechos
    assert not any(h[0] == "modificar" for h in gmail.hechos)  # etiquetas apagadas por defecto
    assert r["borrados_viejos"] == 1 and conn.execute("SELECT 1 FROM emails WHERE id = ?", (viejo,)).fetchone() is None
    assert enviados == ["2 mails para responder"] and r["avisos_enviados"] == 1
    assert "por_categoria" not in r


def test_reglas_automaticas_desactivadas(conn, monkeypatch, cliente):
    mail = agregar_mail(conn, de="promo@betx.com")
    db.guardar_decision(conn, mail, "regla", "papelera", "Regla", "regla")
    assert cliente.post("/api/ajustes", json={"reglas_automaticas": False}).status_code == 200
    assert cliente.get("/api/ajustes").json()["reglas_automaticas"] is False
    r, gmail, enviados = _actualizar_con(monkeypatch, conn, {})
    assert r["aplicadas_por_reglas"] == 0 and enviados == []
    assert not any(h[0] in ("papelera", "modificar") for h in gmail.hechos)
