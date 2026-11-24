"""Avisos al celular y borradores de respuesta."""
import base64

import pytest

from conftest import GmailFalso, agregar_mail
from mail_crm import avisos, db, gmail_client, panel, redactor

SUSCRIPCION = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
               "keys": {"p256dh": "clave-publica-del-navegador", "auth": "secreto-corto"}}


@pytest.mark.parametrize("endpoint, valido", [
    ("https://fcm.googleapis.com/fcm/send/x", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
    ("https://web.push.apple.com/x", True),
    ("https://wns2-bn3p.notify.windows.com/w/?token=x", True),
    ("http://fcm.googleapis.com/x", False),                  # sin https
    ("https://fcm.googleapis.com.malo.example/x", False),    # parecido, pero de otro
    ("https://malopush.apple.com/x", False),
    ("https://169.254.169.254/latest", False),               # direcciones internas
    ("https://user@fcm.googleapis.com/x", False),
    ("https://fcm.googleapis.com:8443/x", False),
    ("https://fcm.googleapis.com/" + "x" * 1000, False),
])
def test_solo_servicios_de_push_conocidos(endpoint, valido):
    assert avisos.endpoint_valido(endpoint) is valido


def test_clave_publica_estable(conn):
    clave = avisos.clave_publica(conn)
    assert avisos.clave_publica(conn) == clave
    crudo = base64.urlsafe_b64decode(clave + "=" * (-len(clave) % 4))
    assert len(crudo) == 65 and crudo[0] == 4  # punto P-256 sin comprimir


def test_texto_de_aviso():
    assert avisos.texto_de_aviso({}) is None
    assert avisos.texto_de_aviso({"spam": 3}) is None
    assert avisos.texto_de_aviso({"requiere_respuesta": 1}) == "1 mail para responder"
    assert avisos.texto_de_aviso({"requiere_respuesta": 2, "atencion": 1}) == "2 mails para responder y 1 mail que tenés que ver"


def test_suscribir_probar_y_desuscribir(cliente, conn, monkeypatch):
    enviados = []
    monkeypatch.setattr(avisos, "webpush", lambda sub, datos, **k: enviados.append((sub["endpoint"], datos)))
    assert cliente.post("/api/avisos/probar").status_code == 409  # todavia no hay dispositivos
    malo = {**SUSCRIPCION, "endpoint": "https://mi-servidor.example/robar"}
    assert cliente.post("/api/avisos/suscribir", json=malo).status_code == 422
    assert cliente.post("/api/avisos/suscribir", json=SUSCRIPCION).status_code == 200
    assert cliente.get("/api/avisos/clave").json()["dispositivos"] == 1
    assert cliente.post("/api/avisos/probar").json() == {"enviados": 1}
    assert enviados[0][0] == SUSCRIPCION["endpoint"] and "prueba" in enviados[0][1]
    assert cliente.post("/api/avisos/desuscribir", json={"endpoint": SUSCRIPCION["endpoint"]}).status_code == 200
    assert db.listar_suscripciones(conn) == []


def test_dispositivo_que_ya_no_existe_se_borra(conn, monkeypatch):
    class Respuesta:
        status_code = 410

    def falla(*_a, **_k):
        raise avisos.WebPushException("gone", response=Respuesta())

    db.guardar_suscripcion(conn, SUSCRIPCION["endpoint"], '{"endpoint": "%s", "keys": {}}' % SUSCRIPCION["endpoint"])
    monkeypatch.setattr(avisos, "webpush", falla)
    assert avisos.enviar(conn, "t", "x") == 0
    assert db.listar_suscripciones(conn) == []


def test_el_borrador_va_al_remitente_original_y_nunca_se_envia(conn, cliente, monkeypatch):
    mail = agregar_mail(conn, de="ana@empresa.com")
    gmail = GmailFalso(cabeceras=[
        {"name": "From", "value": "Ana <ana@empresa.com>"},
        {"name": "Subject", "value": "Entrevista\r\nBcc: espia@malo.example"},  # intento de colar otra cabecera
        {"name": "Message-ID", "value": "<abc@empresa.com>"},
    ])
    monkeypatch.setattr(gmail_client, "obtener_servicio", lambda interactivo=True: gmail)
    monkeypatch.setattr(redactor, "redactar_respuesta", lambda m, indicaciones="": "Hola Ana, gracias.")

    assert cliente.post("/api/borrador/redactar", json={"email_id": mail}).json() == {"texto": "Hola Ana, gracias."}
    r = cliente.post("/api/borrador/guardar", json={"email_id": mail, "texto": "Hola Ana, gracias."})
    assert r.status_code == 200 and r.json()["para"] == "ana@empresa.com"
    creado = next(h[1] for h in gmail.hechos if h[0] == "borrador")
    crudo = base64.urlsafe_b64decode(creado["message"]["raw"]).decode()
    cabeceras = crudo.split("\n\n", 1)[0]
    lineas = cabeceras.splitlines()
    assert "To: ana@empresa.com" in lineas
    assert not any(l.lower().startswith(("bcc:", "cc:")) for l in lineas)  # el salto de linea no colo otra cabecera
    assert not any(h[0] == "enviar" for h in gmail.hechos)
    assert not hasattr(gmail_client, "enviar_mail")  # la app no tiene forma de enviar
    assert cliente.post("/api/borrador/guardar", json={"email_id": mail, "texto": "otra"}).status_code == 409
