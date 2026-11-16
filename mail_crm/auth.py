"""Inicio de sesion del panel: contraseña, sesiones y limite de intentos.

- La contraseña NUNCA se guarda: se guarda un "hash" (scrypt), que no se puede revertir.
- La sesion es una cookie firmada con un secreto que solo conoce el servidor.
- Tras varios intentos fallidos se bloquea el login por un rato (frena a quien prueba claves).
"""
import base64
import hashlib
import hmac
import secrets
import time

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
DIAS_DE_SESION = 30
LARGO_MINIMO = 10


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).decode().rstrip("=")


def _desb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _scrypt(clave: str, sal: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(clave.encode("utf-8"), salt=sal, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)


def hash_clave(clave: str) -> str:
    """Convierte la contraseña en un texto seguro para guardar."""
    sal = secrets.token_bytes(16)
    return f"scrypt.{SCRYPT_N}.{SCRYPT_R}.{SCRYPT_P}.{_b64(sal)}.{_b64(_scrypt(clave, sal, SCRYPT_N, SCRYPT_R, SCRYPT_P))}"


def verificar_clave(clave: str, guardado: str) -> bool:
    try:
        tipo, n, r, p, sal, esperado = guardado.split(".")
        if tipo != "scrypt":
            return False
        calculado = _scrypt(clave, _desb64(sal), int(n), int(r), int(p))
        return hmac.compare_digest(calculado, _desb64(esperado))
    except (ValueError, TypeError):
        return False


def nuevo_secreto() -> str:
    return secrets.token_urlsafe(48)


def _firma(secreto: str, vence: str) -> str:
    return _b64(hmac.new(secreto.encode(), f"sesion:{vence}".encode(), hashlib.sha256).digest())


def crear_sesion(secreto: str) -> str:
    """Valor de la cookie: fecha de vencimiento + firma."""
    vence = str(int(time.time()) + DIAS_DE_SESION * 86400)
    return f"{vence}.{_firma(secreto, vence)}"


def sesion_valida(secreto: str, cookie: str | None) -> bool:
    if not cookie or not secreto:
        return False
    vence, _, firma = cookie.partition(".")
    if not vence.isdigit() or int(vence) < time.time():
        return False
    return hmac.compare_digest(firma, _firma(secreto, vence))


class LimiteDeIntentos:
    """Bloquea el login tras demasiados fallos en una ventana de tiempo."""

    def __init__(self, por_ip: int = 5, global_: int = 30, ventana_seg: int = 15 * 60):
        self.por_ip, self.global_, self.ventana = por_ip, global_, ventana_seg
        self._fallos: list[tuple[float, str]] = []

    def _recientes(self) -> list[tuple[float, str]]:
        limite = time.time() - self.ventana
        self._fallos = [f for f in self._fallos if f[0] > limite]
        return self._fallos

    def bloqueado(self, ip: str) -> bool:
        recientes = self._recientes()
        return len(recientes) >= self.global_ or sum(1 for _, i in recientes if i == ip) >= self.por_ip

    def registrar_fallo(self, ip: str) -> None:
        self._fallos.append((time.time(), ip))
