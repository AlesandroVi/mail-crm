"""Preparacion comun de los tests.

Cada test usa una base SQLite nueva y vacia, una contraseña de prueba y un Gmail
falso. Ningun test toca tu Gmail real, tu base de Supabase ni Gemini.

Para correrlos:  .venv\\Scripts\\python.exe -m pytest
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from mail_crm import auth, config  # noqa: E402

# Antes de importar el resto: nada de servicios reales, aunque exista un .env.
config.DATABASE_URL = ""
config.GMAIL_TOKEN_JSON = ""
config.GEMINI_API_KEY = ""
config.TAREA_TOKEN = ""

from fastapi.testclient import TestClient  # noqa: E402

from mail_crm import db, gmail_client, panel  # noqa: E402

CLAVE = "clave-de-prueba-para-tests"
HASH = auth.hash_clave(CLAVE)  # se calcula una sola vez: scrypt es lento a proposito


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "prueba.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "PANEL_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "PANEL_CLAVE_HASH", HASH)
    monkeypatch.setattr(config, "PANEL_SECRETO", auth.nuevo_secreto())
    monkeypatch.setattr(panel, "_version", None)
    monkeypatch.setattr(panel, "intentos", auth.LimiteDeIntentos())

    def sin_gmail(*_a, **_k):
        raise gmail_client.FaltaAutorizacion("Gmail no esta disponible en los tests.")

    monkeypatch.setattr(gmail_client, "obtener_servicio", sin_gmail)


@pytest.fixture
def conn():
    c = db.conectar()
    yield c
    c.close()


def nuevo_cliente(**opciones) -> TestClient:
    return TestClient(panel.app, follow_redirects=False, **opciones)


@pytest.fixture
def cliente():
    """Un navegador con la sesion ya iniciada."""
    c = nuevo_cliente()
    assert c.post("/login", json={"clave": CLAVE}).status_code == 200
    return c


def agregar_mail(conn, gmail_id="m1", de="ana@tienda.com", asunto="Hola", cuerpo="cuerpo",
                 fecha="2099-01-01T10:00:00+00:00", nombre="Ana") -> int:
    """Guarda un mail de prueba y devuelve su id en la base."""
    db.guardar_mail(conn, {"gmail_id": gmail_id, "thread_id": "t-" + gmail_id, "from_email": de,
                           "from_name": nombre, "subject": asunto, "snippet": cuerpo[:50], "body": cuerpo,
                           "received_at": fecha, "labels": ["INBOX"], "is_unread": True})
    return conn.execute("SELECT id FROM emails WHERE gmail_id = ?", (gmail_id,)).fetchone()["id"]


def ultima_decision(conn, email_id):
    return conn.execute("SELECT * FROM decisions WHERE email_id = ? ORDER BY id DESC", (email_id,)).fetchone()


class _Pedido:
    def __init__(self, registro, hecho, respuesta=None):
        self.registro, self.hecho, self.respuesta = registro, hecho, respuesta

    def execute(self):
        self.registro.append(self.hecho)
        return self.respuesta if self.respuesta is not None else {}


class GmailFalso:
    """Imita la parte de la API de Gmail que usa la app y anota lo que se le pidio."""

    def __init__(self, cabeceras=None):
        self.hechos: list = []
        self.cabeceras = cabeceras or []

    def users(self):
        return self

    def messages(self):
        return self

    def drafts(self):
        return _Borradores(self)

    def trash(self, userId, id):
        return _Pedido(self.hechos, ("papelera", id))

    def untrash(self, userId, id):
        return _Pedido(self.hechos, ("sacar de papelera", id))

    def modify(self, userId, id, body):
        return _Pedido(self.hechos, ("modificar", id, tuple(body.get("addLabelIds", [])), tuple(body.get("removeLabelIds", []))))

    def get(self, userId, id, **_k):
        return _Pedido(self.hechos, ("leer", id),
                       {"threadId": "hilo-" + id, "payload": {"headers": self.cabeceras}})


class _Borradores:
    def __init__(self, gmail):
        self.gmail = gmail

    def create(self, userId, body):
        return _Pedido(self.gmail.hechos, ("borrador", body), {"id": "borrador-1"})

    def delete(self, userId, id):
        return _Pedido(self.gmail.hechos, ("borrar borrador", id))
