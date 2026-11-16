"""Panel web del CRM.

Uso:  python -m mail_crm.panel     y abri http://127.0.0.1:8000

En tu PC el servidor solo escucha en 127.0.0.1: nadie mas en la red puede entrar.
Si definiste una contraseña (python -m mail_crm.clave), el panel la pide al entrar.
En un servidor de internet (PANEL_HOST=0.0.0.0) la contraseña es obligatoria.
"""
import hmac
import re
import threading
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import acciones, auth, clasificar, config, db, gmail_client, sync
from .reglas import ACCIONES_DE_REGLA

WEB = Path(__file__).parent / "web"
# mail exacto (a@b.com) o dominio (b.com)
PATRON_VALIDO = re.compile(r"^([a-z0-9._%+-]+@)?[a-z0-9-]+(\.[a-z0-9-]+)+$")
COOKIE = "sesion"
# Lo unico que se puede ver sin iniciar sesion.
RUTAS_PUBLICAS = {"/login", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/salud",
                  "/api/tarea/actualizar"}  # esta ultima se protege con su propio token

app = FastAPI(title="Mail CRM", docs_url=None, redoc_url=None, openapi_url=None)
intentos = auth.LimiteDeIntentos()


def login_activo() -> bool:
    return bool(config.PANEL_CLAVE_HASH and config.PANEL_SECRETO)


def _es_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _ip(request: Request) -> str:
    """IP de quien hace el pedido. En un hosting llega en X-Forwarded-For."""
    if config.PANEL_HOST != "127.0.0.1" and request.headers.get("x-forwarded-for"):
        return request.headers["x-forwarded-for"].split(",")[0].strip()
    return request.client.host if request.client else "desconocida"


@app.middleware("http")
async def seguridad(request: Request, call_next):
    # 1) Rechaza cambios pedidos desde otra pagina web abierta en tu navegador.
    if request.method not in ("GET", "HEAD"):
        origen = request.headers.get("origin")
        if origen and urlparse(origen).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Origen no permitido"}, status_code=403)

    # 2) Si hay contraseña definida, todo lo demas exige una sesion valida.
    ruta = request.url.path
    if login_activo() and ruta not in RUTAS_PUBLICAS:
        if not auth.sesion_valida(config.PANEL_SECRETO, request.cookies.get(COOKIE)):
            if ruta.startswith("/api/"):
                return JSONResponse({"detail": "Tenes que iniciar sesion."}, status_code=401)
            return RedirectResponse("/login", status_code=303)

    respuesta = await call_next(request)
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    respuesta.headers["X-Frame-Options"] = "DENY"
    respuesta.headers["Referrer-Policy"] = "no-referrer"
    return respuesta


@app.get("/")
def inicio():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/login")
def pagina_login(request: Request):
    if not login_activo() or auth.sesion_valida(config.PANEL_SECRETO, request.cookies.get(COOKIE)):
        return RedirectResponse("/", status_code=303)
    return FileResponse(WEB / "login.html", headers={"Cache-Control": "no-store"})


class Login(BaseModel):
    clave: str


@app.post("/login")
def iniciar_sesion(datos: Login, request: Request):
    if not login_activo():
        raise HTTPException(404, "Este panel no usa contraseña.")
    ip = _ip(request)
    if intentos.bloqueado(ip):
        raise HTTPException(429, "Demasiados intentos fallidos. Esperá 15 minutos y probá de nuevo.")
    if not auth.verificar_clave(datos.clave, config.PANEL_CLAVE_HASH):
        intentos.registrar_fallo(ip)
        raise HTTPException(401, "Contraseña incorrecta.")
    respuesta = JSONResponse({"ok": True})
    respuesta.set_cookie(
        COOKIE,
        auth.crear_sesion(config.PANEL_SECRETO),
        max_age=auth.DIAS_DE_SESION * 86400,
        httponly=True,  # el JavaScript de la pagina no puede leerla
        samesite="lax",
        secure=_es_https(request),
    )
    return respuesta


@app.post("/logout")
def cerrar_sesion():
    respuesta = JSONResponse({"ok": True})
    respuesta.delete_cookie(COOKIE)
    return respuesta


# Archivos que necesita el navegador para instalar el panel como app (PWA).
@app.get("/manifest.webmanifest")
def manifiesto():
    return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/icon-{tam}.png")
def icono(tam: int):
    if tam not in (192, 512):
        raise HTTPException(404, "No existe ese icono.")
    return FileResponse(WEB / f"icon-{tam}.png", media_type="image/png")


@app.get("/api/mails")
def api_mails():
    conn = db.conectar()
    try:
        mails = [dict(f) for f in db.mails_con_decision(conn)]
    finally:
        conn.close()
    por_accion = Counter(m["action"] or "pendiente" for m in mails)
    return {
        "mails": mails,
        "por_accion": por_accion,
        "con_efecto": acciones.ACCIONES_CON_EFECTO,
        "login_activo": login_activo(),
    }


@app.get("/api/contactos")
def api_contactos():
    conn = db.conectar()
    try:
        reglas = db.listar_reglas(conn)
        contactos = []
        for c in db.todos_los_contactos(conn):
            regla = db.buscar_regla(reglas, c["email"])
            contactos.append({**dict(c), "regla": dict(regla) if regla else None})
    finally:
        conn.close()
    return {"contactos": contactos}


@app.get("/api/reglas")
def api_reglas():
    conn = db.conectar()
    try:
        return {"reglas": [dict(r) for r in db.listar_reglas(conn)], "acciones": ACCIONES_DE_REGLA}
    finally:
        conn.close()


class ReglaNueva(BaseModel):
    pattern: str
    action: str
    note: str = ""


@app.post("/api/reglas")
def api_guardar_regla(regla: ReglaNueva):
    patron = regla.pattern.strip().lower()
    if not PATRON_VALIDO.match(patron):
        raise HTTPException(422, "El patron tiene que ser un mail (a@tienda.com) o un dominio (tienda.com).")
    if regla.action not in ACCIONES_DE_REGLA:
        raise HTTPException(422, f"Accion invalida. Usa una de: {', '.join(ACCIONES_DE_REGLA)}")
    conn = db.conectar()
    try:
        db.guardar_regla(conn, patron, regla.action, regla.note.strip()[:200])
        cambios = db.reaplicar_reglas(conn)
    finally:
        conn.close()
    return {"ok": True, "decisiones_actualizadas": cambios}


@app.delete("/api/reglas")
def api_borrar_regla(pattern: str):
    conn = db.conectar()
    try:
        if not db.borrar_regla(conn, pattern):
            raise HTTPException(404, "No existe esa regla.")
        cambios = db.reaplicar_reglas(conn)
    finally:
        conn.close()
    return {"ok": True, "decisiones_actualizadas": cambios}


def _gmail():
    """Cliente de Gmail para el panel. Nunca abre el navegador por su cuenta."""
    try:
        return gmail_client.obtener_servicio(interactivo=False)
    except gmail_client.FaltaAutorizacion:
        raise HTTPException(
            409,
            "Falta autorizar el permiso nuevo de Gmail. Corré en la terminal: "
            ".venv\\Scripts\\python.exe -m mail_crm.sync",
        )


_actualizando = threading.Lock()


def _actualizar() -> dict:
    """Trae mails nuevos de Gmail y los clasifica. No aplica nada en Gmail."""
    if not _actualizando.acquire(blocking=False):
        raise HTTPException(409, "Ya hay una actualización en curso. Probá en un minuto.")
    try:
        try:
            traidos = sync.sincronizar(interactivo=False)
        except gmail_client.FaltaAutorizacion:
            raise HTTPException(409, "La sesión de Gmail venció o falta autorizarla.")
        resultado = clasificar.clasificar_pendientes()
        return {"nuevos": traidos["nuevos"], **resultado}
    finally:
        _actualizando.release()


@app.post("/api/actualizar")
def api_actualizar():
    """Boton "Actualizar" del panel (requiere sesion iniciada)."""
    return _actualizar()


@app.post("/api/tarea/actualizar")
def tarea_actualizar(request: Request):
    """Lo llama la tarea programada. No usa sesion: se identifica con TAREA_TOKEN."""
    recibido = request.headers.get("authorization", "")
    if not config.TAREA_TOKEN:
        raise HTTPException(404, "Tarea no configurada.")
    if not hmac.compare_digest(recibido.encode(), f"Bearer {config.TAREA_TOKEN}".encode()):
        raise HTTPException(401, "Token incorrecto.")
    return _actualizar()


@app.get("/salud")
def salud():
    """Para que el hosting sepa que el servidor esta vivo. No muestra datos."""
    return {"ok": True}


class Aplicar(BaseModel):
    decision_ids: list[int] | None = None  # None = todas las pendientes


class Deshacer(BaseModel):
    decision_id: int


@app.post("/api/aplicar")
def api_aplicar(pedido: Aplicar):
    servicio = _gmail()
    conn = db.conectar()
    try:
        return acciones.aplicar_decisiones(conn, servicio, pedido.decision_ids)
    finally:
        conn.close()


@app.post("/api/deshacer")
def api_deshacer(pedido: Deshacer):
    servicio = _gmail()
    conn = db.conectar()
    try:
        acciones.deshacer_decision(conn, servicio, pedido.decision_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    finally:
        conn.close()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    if config.PANEL_HOST != "127.0.0.1" and not login_activo():
        raise SystemExit(
            "No arranco: el panel quedaria abierto a internet sin contraseña.\n"
            "Defini una con: python -m mail_crm.clave"
        )
    print(f"Panel abierto en http://{config.PANEL_HOST}:{config.PANEL_PUERTO}  (Ctrl+C para cerrarlo)")
    print("Pide contraseña al entrar." if login_activo() else "Sin contraseña (solo accesible desde esta PC).")
    uvicorn.run(app, host=config.PANEL_HOST, port=config.PANEL_PUERTO, log_level="warning")
