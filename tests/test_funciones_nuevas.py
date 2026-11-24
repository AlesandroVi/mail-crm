"""Tablero de postulaciones, seguimientos, resumen diario, sugerencias de reglas, etiquetas y estadisticas."""
import json
from datetime import datetime, timedelta, timezone

from conftest import GmailFalso, agregar_mail, mensaje, ultima_decision
from mail_crm import (acciones, aprendizaje, avisos, clasificador, clasificar, config, db, estadisticas, etiquetas,
                      gmail_client, panel, resumen, seguimientos, sync)

AHORA_MS = int(datetime.now(timezone.utc).timestamp() * 1000)
DIA_MS = 86_400_000


# --- La IA detecta busquedas laborales ----------------------------------------------

def test_el_clasificador_limpia_los_datos_laborales(monkeypatch):
    respuesta = [{"id": 1, "categoria": "importante", "confianza": 0.9, "motivo": "oferta", "laboral": True,
                  "empresa": "  Acme\n\nSA " + "x" * 200, "puesto": "Dev"},
                 {"id": 2, "categoria": "spam", "confianza": 0.9, "motivo": "x", "laboral": "si"}]
    monkeypatch.setattr(clasificador, "llamar_modelo", lambda *a, **k: json.dumps(respuesta))
    r = clasificador.clasificar_lote([{"id": 1, "from_email": "a@b.com"}, {"id": 2, "from_email": "c@d.com"}])
    assert r[1]["laboral"] is True and r[1]["empresa"].startswith("Acme SA") and len(r[1]["empresa"]) == 100
    assert r[2]["laboral"] is False  # solo vale true de verdad, no un texto


def test_clasificar_sugiere_postulaciones_sin_repetir(conn, monkeypatch):
    oferta = agregar_mail(conn, "m1", de="rrhh@acme.com", asunto="Entrevista")
    estafa = agregar_mail(conn, "m2", de="x@estafa.com", asunto="Trabajo desde casa")
    otra = agregar_mail(conn, "m3", de="rrhh@acme.com", asunto="Recordatorio entrevista")
    base = {"confianza": 0.95, "motivo": "", "laboral": True, "empresa": "Acme", "puesto": "Backend"}
    monkeypatch.setattr(clasificador, "clasificar_lote", lambda lote: {
        oferta: {**base, "categoria": "requiere_respuesta"},
        estafa: {**base, "categoria": "spam", "empresa": "Estafa"},
        otra: {**base, "categoria": "importante"},
    })
    r = clasificar.clasificar_pendientes()
    tarjetas = db.listar_postulaciones(conn)
    assert len(tarjetas) == 1 and tarjetas[0]["stage"] == "sugerida" and tarjetas[0]["company"] == "Acme"
    assert tarjetas[0]["contact_email"] == "rrhh@acme.com"
    assert r["por_categoria"]["postulacion_sugerida"] == 1


# --- Tablero ----------------------------------------------------------------------------

def test_tablero_de_postulaciones(cliente, conn):
    mail = agregar_mail(conn)
    assert cliente.post("/api/postulaciones", json={"company": "Acme", "stage": "inventada"}).status_code == 422
    assert cliente.post("/api/postulaciones", json={"company": ""}).status_code == 422
    assert cliente.post("/api/postulaciones", json={"company": "Acme", "email_id": 999}).status_code == 404
    pid = cliente.post("/api/postulaciones", json={"company": " Acme ", "role": "Dev", "email_id": mail}).json()["id"]
    p = cliente.get("/api/postulaciones").json()["postulaciones"][0]
    assert p["company"] == "Acme" and p["stage"] == "interesa" and p["contact_email"] == "ana@tienda.com"

    assert cliente.post(f"/api/postulaciones/{pid}", json={"stage": "entrevista", "notes": "jueves 10 h"}).status_code == 200
    p = cliente.get("/api/postulaciones").json()["postulaciones"][0]
    assert p["stage"] == "entrevista" and p["notes"] == "jueves 10 h" and p["company"] == "Acme"
    assert cliente.post(f"/api/postulaciones/{pid}", json={"stage": "x"}).status_code == 422
    assert cliente.post(f"/api/postulaciones/{pid}", json={"notes": "x" * 2001}).status_code == 422
    assert cliente.post("/api/postulaciones/999", json={"stage": "oferta"}).status_code == 404
    assert cliente.delete(f"/api/postulaciones/{pid}").status_code == 200
    assert cliente.delete(f"/api/postulaciones/{pid}").status_code == 404


def test_la_tarjeta_sobrevive_a_la_limpieza(conn):
    mail = agregar_mail(conn, fecha="2001-01-01T00:00:00+00:00")
    db.crear_postulacion(conn, "Acme", email_id=mail, contact_email="ana@tienda.com")
    db.limpiar_viejos(conn, 60, config.MAX_CUERPO)
    assert len(db.listar_postulaciones(conn)) == 1


# --- Seguimientos -------------------------------------------------------------------------

def _gmail_con_enviados():
    hace5, hace4 = AHORA_MS - 5 * DIA_MS, AHORA_MS - 4 * DIA_MS
    return GmailFalso(
        mensajes={
            "s1": mensaje("s1", para="Ana <ana@empresa.com>", asunto="Mi CV", fecha_ms=hace5, hilo="h1"),
            "s2": mensaje("s2", para="Beto <beto@empresa.com>", asunto="Consulta", fecha_ms=hace5, hilo="h2"),
            "s3": mensaje("s3", para="no-reply@banco.com", asunto="Baja", fecha_ms=hace5, hilo="h3"),
            "s4": mensaje("s4", para="yo@gmail.com", asunto="Nota para mi", fecha_ms=hace5, hilo="h4"),
        },
        hilos={
            "h1": {"messages": [mensaje("s1", de="yo@gmail.com", fecha_ms=hace5),
                                mensaje("r1", de="Ana <ana@empresa.com>", fecha_ms=hace4)]},
            "h2": {"messages": [mensaje("s2", de="yo@gmail.com", fecha_ms=hace5),
                                mensaje("s2b", de="yo@gmail.com", fecha_ms=hace4)]},  # solo vos insististe
        },
    )


def test_seguimientos_detecta_quien_no_contesto(conn, cliente):
    gmail = _gmail_con_enviados()
    r = seguimientos.actualizar(gmail, conn)
    assert r == {"enviados_nuevos": 4, "respondidos": 1}
    pendientes = cliente.get("/api/seguimientos").json()["seguimientos"]
    assert [s["to_email"] for s in pendientes] == ["beto@empresa.com"]
    assert seguimientos.actualizar(gmail, conn)["enviados_nuevos"] == 0  # no se guardan dos veces
    sid = pendientes[0]["id"]
    assert cliente.post(f"/api/seguimientos/{sid}/descartar").status_code == 200
    assert cliente.get("/api/seguimientos").json()["seguimientos"] == []
    assert cliente.post("/api/seguimientos/999/descartar").status_code == 404


def test_seguimiento_reciente_todavia_no_aparece(conn):
    gmail = GmailFalso(mensajes={"s1": mensaje("s1", para="ana@empresa.com", asunto="Hola", fecha_ms=AHORA_MS - DIA_MS)})
    seguimientos.actualizar(gmail, conn)
    assert db.seguimientos_pendientes(conn, config.DIAS_SIN_RESPUESTA) == []


# --- Cambiar decisiones a mano y sugerencias de reglas -------------------------------------

def test_cambiar_decision_a_mano(cliente, conn):
    mail = agregar_mail(conn)
    db.guardar_decision(conn, mail, "spam", "papelera", "IA", "gemini")
    assert cliente.post("/api/decisiones/cambiar", json={"email_id": mail, "action": "borrar_todo"}).status_code == 422
    assert cliente.post("/api/decisiones/cambiar", json={"email_id": 999, "action": "dejar"}).status_code == 404
    assert cliente.post("/api/decisiones/cambiar", json={"email_id": mail, "action": "dejar"}).status_code == 200
    d = ultima_decision(conn, mail)
    assert d["action"] == "dejar" and d["model"] == "manual" and d["applied"] == 0
    db.guardar_decision(conn, mail, "spam", "papelera", "IA", "gemini")
    db.marcar_aplicada(conn, ultima_decision(conn, mail)["id"], True)
    assert cliente.post("/api/decisiones/cambiar", json={"email_id": mail, "action": "dejar"}).status_code == 409


def test_las_decisiones_a_mano_no_se_aplican_solas(conn):
    mail = agregar_mail(conn)
    db.guardar_decision(conn, mail, "manual", "papelera", "Elegido por vos", "manual")
    gmail = GmailFalso()
    assert acciones.aplicar_reglas(conn, gmail)["aplicadas"] == 0 and gmail.hechos == []


def test_sugerencias_de_reglas(conn, cliente):
    for i in range(2):  # corregiste dos veces a la IA con el mismo remitente
        m = agregar_mail(conn, f"c{i}", de="jefe@trabajo.com")
        db.guardar_decision(conn, m, "promocion", "archivar", "IA", "gemini")
        db.guardar_decision(conn, m, "manual", "destacar", "Elegido por vos", "manual")
    for i in range(3):  # aplicaste tres veces lo mismo
        m = agregar_mail(conn, f"a{i}", de="ofertas@tienda.com")
        db.guardar_decision(conn, m, "promocion", "archivar", "IA", "gemini")
        db.marcar_aplicada(conn, ultima_decision(conn, m)["id"], True)
    for i in range(2):  # deshiciste dos veces a la IA
        m = agregar_mail(conn, f"d{i}", de="amiga@gmail.com")
        db.guardar_decision(conn, m, "spam", "papelera", "IA", "gemini")
        d = ultima_decision(conn, m)["id"]
        db.marcar_aplicada(conn, d, True)
        db.marcar_aplicada(conn, d, False)

    s = {x["pattern"]: x["action"] for x in cliente.get("/api/sugerencias").json()["sugerencias"]}
    assert s == {"jefe@trabajo.com": "destacar", "ofertas@tienda.com": "archivar", "amiga@gmail.com": "dejar"}

    assert cliente.post("/api/sugerencias/ignorar", json={"pattern": "jefe@trabajo.com"}).status_code == 200
    db.guardar_regla(conn, "tienda.com", "archivar")
    s = {x["pattern"] for x in aprendizaje.sugerencias(conn)}
    assert s == {"amiga@gmail.com"}  # la ignorada y la que ya tiene regla no vuelven


# --- Etiquetas de Gmail ---------------------------------------------------------------------

def test_etiquetas_en_gmail(conn):
    ia = agregar_mail(conn, "m1")
    regla = agregar_mail(conn, "m2", de="promo@betx.com")
    borrado = agregar_mail(conn, "m3", de="x@y.com")
    db.guardar_decision(conn, ia, "requiere_respuesta", "borrador", "IA", "gemini")
    db.guardar_decision(conn, regla, "regla", "papelera", "Regla", "regla")
    db.guardar_decision(conn, borrado, "spam", "papelera", "IA", "gemini")
    gmail = GmailFalso(etiquetas=[{"name": "CRM/Spam", "id": "Label_existente"}])

    from googleapiclient.errors import HttpError

    class Resp(dict):
        status = 404
        reason = "Not Found"

    original = gmail.modify

    def modify(userId, id, body):
        if id == "m3":
            raise HttpError(Resp(), b"no existe")
        return original(userId, id, body)

    gmail.modify = modify
    assert etiquetas.etiquetar(gmail, conn) == 1
    creadas = [h[1] for h in gmail.hechos if h[0] == "crear etiqueta"]
    assert "CRM/Para responder" in creadas and "CRM/Spam" not in creadas  # la que ya existia no se duplica
    id_para_responder = next(e["id"] for e in gmail.etiquetas if e["name"] == "CRM/Para responder")
    assert ("modificar", "m1", (id_para_responder,), ()) in gmail.hechos
    assert etiquetas.etiquetar(gmail, conn) == 0  # nada pendiente: ni el de la regla ni el que ya no existe
    assert not any(h[0] == "modificar" and h[1] == "m2" for h in gmail.hechos)


# --- Resumen diario ---------------------------------------------------------------------------

def test_resumen_una_vez_por_dia(conn, monkeypatch):
    monkeypatch.setattr(resumen, "hoy_local", lambda: datetime(2026, 11, 24, 7, 0))
    assert not resumen.toca_hoy(conn)  # antes de las 8
    monkeypatch.setattr(resumen, "hoy_local", lambda: datetime(2026, 11, 24, 9, 0))
    assert resumen.toca_hoy(conn)
    r = resumen.generar(conn)
    assert "nada urgente" in r["texto"] and r["cuentas"] == {}
    assert not resumen.toca_hoy(conn)
    assert resumen.guardado(conn)["fecha"] == "2026-11-24"


def test_resumen_con_ia_y_sin_ia(conn, monkeypatch):
    mail = agregar_mail(conn, fecha=datetime.now(timezone.utc).isoformat(timespec="seconds"), asunto="Entrevista")
    db.guardar_decision(conn, mail, "requiere_respuesta", "borrador", "IA", "gemini")
    pedidos = []
    monkeypatch.setattr(clasificador, "llamar_modelo", lambda m, **k: pedidos.append(m) or "Tenés que contestarle a Ana.")
    r = resumen.generar(conn)
    assert r["texto"] == "Tenés que contestarle a Ana." and r["cuentas"] == {"para responder": 1}
    assert "Entrevista" in pedidos[0]

    def falla(*a, **k):
        raise clasificador.CuotaAgotada("sin cuota")

    monkeypatch.setattr(clasificador, "llamar_modelo", falla)
    assert "Para responder: Ana - Entrevista" in resumen.generar(conn)["texto"]  # sin IA, lista simple
    assert resumen.texto_de_aviso({"para responder": 1}) == "Tu resumen de hoy: 1 para responder."


def test_actualizar_manda_un_solo_aviso_con_el_resumen(conn, monkeypatch):
    enviados = []
    monkeypatch.setattr(sync, "sincronizar", lambda interactivo: {"nuevos": 0, "revisados": 0})
    monkeypatch.setattr(clasificar, "clasificar_pendientes",
                        lambda: {"clasificados": 0, "pendientes": 0, "aviso": None, "por_categoria": {"requiere_respuesta": 1}})
    monkeypatch.setattr(gmail_client, "obtener_servicio", lambda interactivo=True: GmailFalso())
    monkeypatch.setattr(avisos, "enviar", lambda c, t, texto: enviados.append(texto) or 1)
    monkeypatch.setattr(resumen, "toca_hoy", lambda c: True)
    monkeypatch.setattr(clasificador, "llamar_modelo", lambda *a, **k: "Resumen.")
    r = panel._actualizar()
    assert r["resumen"] is True and len(enviados) == 1 and enviados[0].startswith("Tu resumen de hoy")
    assert resumen.guardado(conn)["texto"]


def test_api_resumen(cliente, monkeypatch):
    assert cliente.get("/api/resumen").json() == {"resumen": None}
    assert "nada urgente" in cliente.post("/api/resumen/generar").json()["resumen"]["texto"]


# --- Estadisticas -------------------------------------------------------------------------------

def test_estadisticas(conn, cliente):
    hoy = datetime.now(timezone.utc)
    for i in range(3):
        m = agregar_mail(conn, f"m{i}", de=f"x{i % 2}@a.com", fecha=(hoy - timedelta(days=i * 8)).isoformat(timespec="seconds"))
        db.guardar_decision(conn, m, "spam", "papelera", "IA", "gemini")
    db.crear_postulacion(conn, "Acme", stage="entrevista")
    d = cliente.get("/api/estadisticas").json()
    assert len(d["semanas"]) == estadisticas.SEMANAS and sum(s["mails"] for s in d["semanas"]) == 3
    assert d["por_categoria"] == {"spam": 3} and d["remitentes"][0]["mails"] == 2
    assert d["postulaciones"]["entrevista"] == 1 and d["total"] == 3
