"""Panel web del CRM.

Uso:  python -m mail_crm.panel     y abri http://127.0.0.1:8000

En tu PC el servidor solo escucha en 127.0.0.1: nadie mas en la red puede entrar.
Si definiste una contraseña (python -m mail_crm.clave), el panel la pide al entrar.
En un servidor de internet (PANEL_HOST=0.0.0.0) la contraseña es obligatoria.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from googleapiclient.errors import HttpError
from pydantic import BaseModel, Field

from . import (acciones, aprendizaje, auth, avisos, clasificador, clasificar, config, db, estadisticas, etiquetas,
               gmail_client, redactor, resumen, seguimientos, sync)
from .reglas import ACCIONES_DE_REGLA

WEB = Path(__file__).parent / "web"
# mail exacto (a@b.com) o dominio (b.com)
PATRON_VALIDO = re.compile(r"^([a-z0-9._%+-]+@)?[a-z0-9-]+(\.[a-z0-9-]+)+$")
COOKIE = "sesion"
COOKIE_DISPOSITIVO = "dispositivo"
# Lo unico que se puede ver sin iniciar sesion.
RUTAS_PUBLICAS = {"/login", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/salud", "/sw.js",
                  "/api/tarea/actualizar"}  # esta ultima se protege con su propio token
# Ningun pedido legitimo del panel pesa mas que esto (el mas grande es un borrador de 10.000 caracteres).
MAX_PEDIDO = 256 * 1024
# Sin contraseña, el panel solo atiende pedidos hechos desde esta misma PC.
DIRECCIONES_LOCALES = {"127.0.0.1", "::1"}
NOMBRES_LOCALES = {"127.0.0.1", "localhost", "[::1]"}


def login_activo() -> bool:
    return bool(config.PANEL_CLAVE_HASH and config.PANEL_SECRETO)


@asynccontextmanager
async def _al_arrancar(_app):
    # Esta comprobacion corre siempre, se arranque como se arranque (python -m o uvicorn).
    if config.PANEL_HOST not in DIRECCIONES_LOCALES and not login_activo():
        raise RuntimeError(
            "No arranco: el panel quedaria abierto a internet sin contraseña. "
            "Defini una con: python -m mail_crm.clave"
        )
    yield


app = FastAPI(title="Mail CRM", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_al_arrancar)
intentos = auth.LimiteDeIntentos()
_un_login_a_la_vez = threading.Lock()  # cada login usa ~32 MB de memoria (scrypt)


def _registrar(evento: str, request: Request | None = None) -> None:
    """Deja una linea en el registro del servidor. Nunca incluye contraseñas ni contenido de mails."""
    origen = ""
    if request is not None:
        cliente = request.client.host if request.client else "?"
        origen = f" desde={request.headers.get('x-forwarded-for', cliente)[:100]}"
    print(f"[seguridad] {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {evento}{origen}", flush=True)


def _es_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


_version: str | None = None


def _sello() -> str:
    """Lo que hace validas a las sesiones. Si cambia, todas las cookies anteriores dejan de servir.

    Tiene dos partes: una version guardada en la base (cambia al cerrar sesion)
    y el hash de la contraseña (cambia al cambiar la contraseña).
    """
    global _version
    if _version is None:
        conn = db.conectar()
        try:
            _version = db.leer_ajuste(conn, "version_sesion", "0")
        finally:
            conn.close()
    return f"{_version}:{config.PANEL_CLAVE_HASH}"


def _sesion_ok(request: Request) -> bool:
    return auth.sesion_valida(config.PANEL_SECRETO, request.cookies.get(COOKIE), _sello())


def _politica_de_contenido() -> str:
    """Content-Security-Policy: el navegador solo ejecuta los scripts y estilos de nuestras paginas.

    Cada <script> y <style> se identifica por su huella (sha256). Un script que
    alguien lograra colar en la pagina no tiene huella conocida y no corre.
    """
    huellas = {"script": set(), "style": set()}
    for pagina in ("index.html", "login.html"):
        html = (WEB / pagina).read_text(encoding="utf-8").replace("\r\n", "\n")  # como lo lee el navegador
        for tipo, texto in re.findall(r"<(script|style)>(.*?)</\1>", html, re.S):
            huellas[tipo].add("'sha256-" + base64.b64encode(hashlib.sha256(texto.encode("utf-8")).digest()).decode() + "'")
    return (
        "default-src 'none'; "
        f"script-src {' '.join(sorted(huellas['script']))}; "
        f"style-src {' '.join(sorted(huellas['style']))}; "
        "img-src 'self'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )


CSP = _politica_de_contenido()


def _rechazo(request: Request):
    """Devuelve la respuesta de rechazo si el pedido no puede pasar, o None si puede."""
    # 0) Sin contraseña, solo se atiende a esta PC. Revisar tambien el nombre (Host) frena a
    #    una pagina web que intente hacerse pasar por 127.0.0.1 ("DNS rebinding").
    if not login_activo():
        cliente = request.client.host if request.client else ""
        nombre = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
        if cliente not in DIRECCIONES_LOCALES or nombre not in NOMBRES_LOCALES:
            return JSONResponse({"detail": "Sin contraseña, este panel solo se puede usar desde esta PC."},
                                status_code=403)

    # 1) Pedidos exageradamente grandes: se cortan antes de leerlos.
    largo = request.headers.get("content-length", "0")
    if not largo.isdigit() or int(largo) > MAX_PEDIDO:
        return JSONResponse({"detail": "El pedido es demasiado grande."}, status_code=413)

    # 2) Rechaza cambios pedidos desde otra pagina web abierta en tu navegador.
    if request.method not in ("GET", "HEAD"):
        origen = request.headers.get("origin")
        if origen and urlparse(origen).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Origen no permitido"}, status_code=403)

    # 3) Si hay contraseña definida, todo lo demas exige una sesion valida.
    ruta = request.url.path
    if login_activo() and ruta not in RUTAS_PUBLICAS and not _sesion_ok(request):
        if ruta.startswith("/api/"):
            return JSONResponse({"detail": "Tenes que iniciar sesion."}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    return None


@app.middleware("http")
async def seguridad(request: Request, call_next):
    respuesta = _rechazo(request) or await call_next(request)
    # Las cabeceras de seguridad van en TODAS las respuestas, tambien en los rechazos.
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    respuesta.headers["X-Frame-Options"] = "DENY"
    respuesta.headers["Referrer-Policy"] = "no-referrer"
    respuesta.headers["Content-Security-Policy"] = CSP
    if _es_https(request):  # el navegador recuerda por un año que este sitio solo se usa por https
        respuesta.headers["Strict-Transport-Security"] = "max-age=31536000"
    return respuesta


@app.get("/")
def inicio():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/login")
def pagina_login(request: Request):
    if not login_activo() or _sesion_ok(request):
        return RedirectResponse("/", status_code=303)
    return FileResponse(WEB / "login.html", headers={"Cache-Control": "no-store"})


class Login(BaseModel):
    clave: str = Field(max_length=200)


@app.post("/login")
def iniciar_sesion(datos: Login, request: Request):
    if not login_activo():
        raise HTTPException(404, "Este panel no usa contraseña.")
    # Un navegador que ya entro bien alguna vez tiene su propia cuenta de intentos.
    dispositivo = auth.dispositivo_valido(config.PANEL_SECRETO, request.cookies.get(COOKIE_DISPOSITIVO))
    tipo = "dispositivo conocido" if dispositivo else "dispositivo nuevo"
    if intentos.bloqueado(dispositivo):
        _registrar(f"login bloqueado ({tipo})", request)
        raise HTTPException(429, "Demasiados intentos fallidos. Esperá 15 minutos y probá de nuevo.")
    with _un_login_a_la_vez:
        correcta = auth.verificar_clave(datos.clave, config.PANEL_CLAVE_HASH)
    if not correcta:
        intentos.registrar_fallo(dispositivo)
        _registrar(f"login fallido ({tipo})", request)
        raise HTTPException(401, "Contraseña incorrecta.")
    _registrar(f"login correcto ({tipo})", request)
    respuesta = JSONResponse({"ok": True})
    opciones = {"httponly": True, "samesite": "lax", "secure": _es_https(request)}  # httponly: el JavaScript no puede leerla
    respuesta.set_cookie(
        COOKIE, auth.crear_sesion(config.PANEL_SECRETO, _sello()), max_age=auth.DIAS_DE_SESION * 86400, **opciones
    )
    if not dispositivo:
        respuesta.set_cookie(
            COOKIE_DISPOSITIVO, auth.crear_dispositivo(config.PANEL_SECRETO), max_age=365 * 86400, **opciones
        )
    return respuesta


@app.post("/logout")
def cerrar_sesion(request: Request):
    """Cierra la sesion en TODOS los dispositivos: cambia la version y las cookies viejas dejan de valer."""
    global _version
    _registrar("cierre de sesion en todos los dispositivos", request)
    nueva = secrets.token_hex(8)
    conn = db.conectar()
    try:
        db.guardar_ajuste(conn, "version_sesion", nueva)
    finally:
        conn.close()
    _version = nueva
    respuesta = JSONResponse({"ok": True})
    respuesta.delete_cookie(COOKIE)
    return respuesta


# Archivos que necesita el navegador para instalar el panel como app (PWA).
@app.get("/manifest.webmanifest")
def manifiesto():
    return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    """El programita que recibe los avisos en el celular. No tiene datos tuyos."""
    return FileResponse(WEB / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


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
        "dias_de_retencion": config.DIAS_DE_RETENCION,
    }


# --- Ficha de contacto: historial de mails y notas tuyas ------------------------

class Notas(BaseModel):
    notas: str = Field("", max_length=2000)


@app.get("/api/contactos/{contacto_id}")
def api_contacto(contacto_id: int):
    conn = db.conectar()
    try:
        contacto = db.contacto_por_id(conn, contacto_id)
        if contacto is None:
            raise HTTPException(404, "No existe ese contacto.")
        regla = db.buscar_regla(db.listar_reglas(conn), contacto["email"])
        mails = [dict(m) for m in db.mails_de_contacto(conn, contacto_id)]
    finally:
        conn.close()
    return {"contacto": dict(contacto), "regla": dict(regla) if regla else None, "mails": mails,
            "dias_de_retencion": config.DIAS_DE_RETENCION}


@app.post("/api/contactos/{contacto_id}/notas")
def api_guardar_notas(contacto_id: int, pedido: Notas):
    conn = db.conectar()
    try:
        if not db.guardar_notas(conn, contacto_id, pedido.notas.strip()):
            raise HTTPException(404, "No existe ese contacto.")
    finally:
        conn.close()
    return {"ok": True}


# --- Ajustes ---------------------------------------------------------------------

class Ajustes(BaseModel):
    reglas_automaticas: bool | None = None
    etiquetas_gmail: bool | None = None


# Ajuste -> valor por defecto. Las reglas automaticas vienen prendidas; las etiquetas, apagadas.
AJUSTES = {"reglas_automaticas": "1", "etiquetas_gmail": "0"}


def _ajuste(conn, nombre: str) -> bool:
    return db.leer_ajuste(conn, nombre, AJUSTES[nombre]) == "1"


def _reglas_automaticas(conn) -> bool:
    return _ajuste(conn, "reglas_automaticas")


@app.get("/api/ajustes")
def api_ajustes():
    conn = db.conectar()
    try:
        return {**{n: _ajuste(conn, n) for n in AJUSTES}, "dias_de_retencion": config.DIAS_DE_RETENCION,
                "hora_resumen": config.HORA_RESUMEN, "dias_sin_respuesta": config.DIAS_SIN_RESPUESTA}
    finally:
        conn.close()


@app.post("/api/ajustes")
def api_guardar_ajustes(pedido: Ajustes, request: Request):
    conn = db.conectar()
    try:
        for nombre, valor in pedido.model_dump().items():
            if valor is not None:
                db.guardar_ajuste(conn, nombre, "1" if valor else "0")
                _registrar(f"ajuste {nombre} = {'si' if valor else 'no'}", request)
    finally:
        conn.close()
    return {"ok": True}


# --- Tablero de postulaciones ---------------------------------------------------------

class Postulacion(BaseModel):
    company: str = Field(min_length=1, max_length=100)
    role: str = Field("", max_length=120)
    notes: str = Field("", max_length=2000)
    stage: str = "interesa"
    email_id: int | None = None


class CambioPostulacion(BaseModel):
    company: str | None = Field(None, min_length=1, max_length=100)
    role: str | None = Field(None, max_length=120)
    notes: str | None = Field(None, max_length=2000)
    stage: str | None = None


def _etapa_valida(etapa: str | None) -> None:
    if etapa is not None and etapa not in config.ETAPAS_POSTULACION:
        raise HTTPException(422, "Etapa inválida.")


@app.get("/api/postulaciones")
def api_postulaciones():
    conn = db.conectar()
    try:
        return {"postulaciones": [dict(p) for p in db.listar_postulaciones(conn)], "etapas": config.ETAPAS_POSTULACION}
    finally:
        conn.close()


@app.post("/api/postulaciones")
def api_crear_postulacion(p: Postulacion):
    _etapa_valida(p.stage)
    conn = db.conectar()
    try:
        contacto = asunto = None
        if p.email_id is not None:
            mail = db.mail_para_responder(conn, p.email_id)
            if mail is None:
                raise HTTPException(404, "No existe ese mail.")
            contacto, asunto = mail["from_email"], mail["subject"]
        nuevo = db.crear_postulacion(conn, p.company.strip(), p.role.strip(), p.stage, p.notes.strip(),
                                     p.email_id, contacto, asunto)
    finally:
        conn.close()
    return {"id": nuevo}


@app.post("/api/postulaciones/{postulacion_id}")
def api_cambiar_postulacion(postulacion_id: int, cambio: CambioPostulacion):
    _etapa_valida(cambio.stage)
    campos = {k: (v.strip() if isinstance(v, str) else v) for k, v in cambio.model_dump().items()}
    conn = db.conectar()
    try:
        if not db.actualizar_postulacion(conn, postulacion_id, campos):
            raise HTTPException(404, "No existe esa postulación.")
    finally:
        conn.close()
    return {"ok": True}


@app.delete("/api/postulaciones/{postulacion_id}")
def api_borrar_postulacion(postulacion_id: int):
    conn = db.conectar()
    try:
        if not db.borrar_postulacion(conn, postulacion_id):
            raise HTTPException(404, "No existe esa postulación.")
    finally:
        conn.close()
    return {"ok": True}


# --- Seguimientos y resumen -------------------------------------------------------------

@app.get("/api/seguimientos")
def api_seguimientos():
    conn = db.conectar()
    try:
        return {"seguimientos": [dict(s) for s in db.seguimientos_pendientes(conn, config.DIAS_SIN_RESPUESTA)],
                "dias": config.DIAS_SIN_RESPUESTA}
    finally:
        conn.close()


@app.post("/api/seguimientos/{seguimiento_id}/descartar")
def api_descartar_seguimiento(seguimiento_id: int):
    conn = db.conectar()
    try:
        if not db.descartar_seguimiento(conn, seguimiento_id):
            raise HTTPException(404, "No existe ese seguimiento.")
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/resumen")
def api_resumen():
    conn = db.conectar()
    try:
        return {"resumen": resumen.guardado(conn)}
    finally:
        conn.close()


@app.post("/api/resumen/generar")
def api_generar_resumen():
    conn = db.conectar()
    try:
        return {"resumen": resumen.generar(conn)}
    finally:
        conn.close()


# --- Cambiar una decision a mano y sugerencias de reglas ---------------------------------

class CambioDecision(BaseModel):
    email_id: int
    action: str


@app.post("/api/decisiones/cambiar")
def api_cambiar_decision(pedido: CambioDecision):
    if pedido.action not in ACCIONES_DE_REGLA:
        raise HTTPException(422, f"Accion invalida. Usa una de: {', '.join(ACCIONES_DE_REGLA)}")
    conn = db.conectar()
    try:
        if db.mail_para_responder(conn, pedido.email_id) is None:
            raise HTTPException(404, "No existe ese mail.")
        anterior = db.ultima_decision(conn, pedido.email_id)
        if anterior is not None and anterior["applied"]:
            raise HTTPException(409, "Esa acción ya se aplicó en Gmail: primero deshacela.")
        db.guardar_decision(conn, pedido.email_id, "manual", pedido.action, "Elegido por vos", "manual")
    finally:
        conn.close()
    return {"ok": True}


class IgnorarSugerencia(BaseModel):
    pattern: str = Field(min_length=3, max_length=200)


@app.get("/api/sugerencias")
def api_sugerencias():
    conn = db.conectar()
    try:
        return {"sugerencias": aprendizaje.sugerencias(conn)}
    finally:
        conn.close()


@app.post("/api/sugerencias/ignorar")
def api_ignorar_sugerencia(pedido: IgnorarSugerencia):
    conn = db.conectar()
    try:
        aprendizaje.ignorar(conn, pedido.pattern)
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/estadisticas")
def api_estadisticas():
    conn = db.conectar()
    try:
        return estadisticas.calcular(conn)
    finally:
        conn.close()


# --- Avisos al celular -------------------------------------------------------------

class Suscripcion(BaseModel):
    endpoint: str = Field(max_length=avisos.LARGO_MAXIMO)
    keys: dict[str, str]


class Desuscripcion(BaseModel):
    endpoint: str = Field(max_length=avisos.LARGO_MAXIMO)


@app.get("/api/avisos/clave")
def api_clave_avisos():
    conn = db.conectar()
    try:
        return {"clave": avisos.clave_publica(conn), "dispositivos": len(db.listar_suscripciones(conn))}
    finally:
        conn.close()


@app.post("/api/avisos/suscribir")
def api_suscribir(pedido: Suscripcion, request: Request):
    limpia = avisos.suscripcion_valida(pedido.model_dump())
    if limpia is None:
        raise HTTPException(422, "Este navegador usa un servicio de avisos que el panel no reconoce.")
    conn = db.conectar()
    try:
        db.guardar_suscripcion(conn, limpia["endpoint"], json.dumps(limpia))
    finally:
        conn.close()
    _registrar("avisos activados en un dispositivo", request)
    return {"ok": True}


@app.post("/api/avisos/desuscribir")
def api_desuscribir(pedido: Desuscripcion):
    conn = db.conectar()
    try:
        db.borrar_suscripcion(conn, pedido.endpoint)
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/avisos/probar")
def api_probar_avisos():
    conn = db.conectar()
    try:
        enviados = avisos.enviar(conn, "Mail CRM", "Aviso de prueba: los avisos funcionan en este dispositivo.")
    finally:
        conn.close()
    if not enviados:
        raise HTTPException(409, "No hay ningún dispositivo con los avisos activados.")
    return {"enviados": enviados}


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
    """Trae mails nuevos de Gmail, los clasifica y despues:
      - aplica en Gmail lo que dicen TUS reglas (si las reglas automaticas estan activadas);
        lo que decide la IA sigue esperando que lo confirmes;
      - borra de la base los mails viejos;
      - avisa al celular si hay mails para responder o que tenes que ver.
    Si alguno de esos pasos falla, la actualizacion igual termina.
    """
    if not _actualizando.acquire(blocking=False):
        raise HTTPException(409, "Ya hay una actualización en curso. Probá en un minuto.")
    try:
        try:
            traidos = sync.sincronizar(interactivo=False)
        except gmail_client.FaltaAutorizacion as e:
            raise HTTPException(409, f"No se pudo entrar a Gmail. {e}")
        resultado = clasificar.clasificar_pendientes()
        por_categoria = resultado.pop("por_categoria", {})
        extra = {"aplicadas_por_reglas": 0, "borrados_viejos": 0, "avisos_enviados": 0, "etiquetados": 0,
                 "seguimientos_respondidos": 0, "postulaciones_sugeridas": por_categoria.get("postulacion_sugerida", 0),
                 "resumen": False}
        conn = db.conectar()
        try:
            def paso(nombre, funcion):
                """Corre un paso extra; si falla, lo anota y sigue con el resto."""
                try:
                    return funcion()
                except Exception as e:
                    _registrar(f"fallo el paso '{nombre}' ({type(e).__name__})")
                    return None

            servicio = paso("conectar con Gmail", lambda: gmail_client.obtener_servicio(interactivo=False))
            if servicio is not None:
                if _reglas_automaticas(conn):
                    hecho = paso("reglas automaticas", lambda: acciones.aplicar_reglas(conn, servicio))
                    if hecho:
                        extra["aplicadas_por_reglas"] = hecho["aplicadas"]
                        if hecho["aplicadas"] or hecho["errores"]:
                            _registrar(f"reglas automaticas: {hecho['aplicadas']} aplicadas, {len(hecho['errores'])} con error")
                if _ajuste(conn, "etiquetas_gmail"):
                    extra["etiquetados"] = paso("etiquetas", lambda: etiquetas.etiquetar(servicio, conn)) or 0
                hecho = paso("seguimientos", lambda: seguimientos.actualizar(servicio, conn))
                if hecho:
                    extra["seguimientos_respondidos"] = hecho["respondidos"]
            extra["borrados_viejos"] = paso("limpieza", lambda: db.limpiar_viejos(
                conn, config.DIAS_DE_RETENCION, config.MAX_CUERPO)) or 0

            texto = avisos.texto_de_aviso(por_categoria)
            if resumen.toca_hoy(conn):
                hecho = paso("resumen diario", lambda: resumen.generar(conn))
                if hecho:
                    extra["resumen"] = True
                    texto = resumen.texto_de_aviso(hecho["cuentas"])  # un solo aviso: el del resumen
            if texto:
                extra["avisos_enviados"] = paso("avisos", lambda: avisos.enviar(conn, "Mail CRM", texto)) or 0
        finally:
            conn.close()
        return {"nuevos": traidos["nuevos"], **resultado, **extra}
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
def api_aplicar(pedido: Aplicar, request: Request):
    servicio = _gmail()
    conn = db.conectar()
    try:
        resultado = acciones.aplicar_decisiones(conn, servicio, pedido.decision_ids)
    finally:
        conn.close()
    _registrar(f"acciones aplicadas en Gmail: {resultado['aplicadas']}, con error: {len(resultado['errores'])}", request)
    return resultado


@app.post("/api/deshacer")
def api_deshacer(pedido: Deshacer, request: Request):
    _registrar("accion deshecha en Gmail", request)
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


# --- Borradores de respuesta ----------------------------------------------
# La IA redacta, vos corregis, y recien al tocar "Guardar en Gmail" se crea el
# borrador. El panel no tiene ninguna forma de ENVIAR un mail.

class Redactar(BaseModel):
    email_id: int
    indicaciones: str = Field("", max_length=config.MAX_INDICACIONES)


class GuardarBorrador(BaseModel):
    email_id: int
    texto: str = Field(min_length=1, max_length=config.MAX_BORRADOR)


class BorrarBorrador(BaseModel):
    email_id: int


def _mail_o_404(conn, email_id: int) -> dict:
    mail = db.mail_para_responder(conn, email_id)
    if mail is None:
        raise HTTPException(404, "No existe ese mail.")
    return dict(mail)


@app.post("/api/borrador/redactar")
def api_redactar(pedido: Redactar):
    """Le pide a la IA el texto de una respuesta y lo guarda solo en el panel."""
    conn = db.conectar()
    try:
        mail = _mail_o_404(conn, pedido.email_id)
        if mail["gmail_draft_id"]:
            raise HTTPException(409, "Ese borrador ya está en Gmail. Borralo de Gmail para redactarlo de nuevo.")
        try:
            texto = redactor.redactar_respuesta(mail, pedido.indicaciones)
        except clasificador.CuotaAgotada:
            raise HTTPException(429, "Se agotó la cuota gratuita de Gemini por ahora. Probá más tarde.")
        except clasificador.NoDisponible:
            raise HTTPException(503, "Gemini está saturado en este momento. Probá en un rato.")
        except clasificador.ErrorIA as e:
            print(f"Error al redactar un borrador: {e}")
            raise HTTPException(502, "La IA no pudo redactar el borrador. Probá de nuevo.")
        db.guardar_borrador(conn, pedido.email_id, texto, clasificador.modelo_usado)
    finally:
        conn.close()
    return {"texto": texto}


@app.post("/api/borrador/guardar")
def api_guardar_borrador(pedido: GuardarBorrador):
    """Crea el borrador en Gmail con el texto que quedo en el panel. No envia nada."""
    texto = pedido.texto.strip()
    if not texto:
        raise HTTPException(422, "El borrador está vacío.")
    servicio = _gmail()
    conn = db.conectar()
    try:
        mail = _mail_o_404(conn, pedido.email_id)
        if mail["gmail_draft_id"]:
            raise HTTPException(409, "Ese borrador ya está guardado en Gmail.")
        db.guardar_borrador(conn, pedido.email_id, texto)
        try:
            borrador_id, destinatario = gmail_client.crear_borrador(servicio, mail["gmail_id"], texto)
        except ValueError as e:
            raise HTTPException(422, str(e))
        except HttpError as e:
            raise HTTPException(502, f"No se pudo guardar el borrador: {acciones.explicar(e)}.")
        db.marcar_borrador_en_gmail(conn, pedido.email_id, borrador_id)
    finally:
        conn.close()
    return {"ok": True, "para": destinatario}


@app.post("/api/borrador/borrar")
def api_borrar_borrador(pedido: BorrarBorrador):
    """Saca el borrador de Gmail. El texto queda en el panel por si lo queres volver a usar."""
    servicio = _gmail()
    conn = db.conectar()
    try:
        mail = _mail_o_404(conn, pedido.email_id)
        if not mail["gmail_draft_id"]:
            raise HTTPException(404, "Ese mail no tiene un borrador guardado en Gmail.")
        try:
            gmail_client.borrar_borrador(servicio, mail["gmail_draft_id"])
        except HttpError as e:
            raise HTTPException(502, f"No se pudo borrar el borrador: {acciones.explicar(e)}.")
        db.marcar_borrador_en_gmail(conn, pedido.email_id, None)
    finally:
        conn.close()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    if config.PANEL_HOST not in DIRECCIONES_LOCALES and not login_activo():
        raise SystemExit(
            "No arranco: el panel quedaria abierto a internet sin contraseña.\n"
            "Defini una con: python -m mail_crm.clave"
        )
    print(f"Panel abierto en http://{config.PANEL_HOST}:{config.PANEL_PUERTO}  (Ctrl+C para cerrarlo)")
    print("Pide contraseña al entrar." if login_activo() else "Sin contraseña (solo accesible desde esta PC).")
    uvicorn.run(app, host=config.PANEL_HOST, port=config.PANEL_PUERTO, log_level="warning")
