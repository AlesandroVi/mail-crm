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


def _firma(secreto: str, texto: str) -> str:
    return _b64(hmac.new(secreto.encode(), texto.encode(), hashlib.sha256).digest())


def crear_sesion(secreto: str, sello: str = "") -> str:
    """Valor de la cookie: fecha de vencimiento + firma.

    El `sello` entra en la firma: cuando cambia (al cerrar sesion o al cambiar
    la contraseña), todas las cookies anteriores dejan de valer.
    """
    vence = str(int(time.time()) + DIAS_DE_SESION * 86400)
    return f"{vence}.{_firma(secreto, f'sesion:{vence}:{sello}')}"


def sesion_valida(secreto: str, cookie: str | None, sello: str = "") -> bool:
    if not cookie or not secreto:
        return False
    vence, _, firma = cookie.partition(".")
    if not vence.isdigit() or int(vence) < time.time():
        return False
    return hmac.compare_digest(firma.encode(), _firma(secreto, f"sesion:{vence}:{sello}").encode())


def crear_dispositivo(secreto: str) -> str:
    """Cookie que identifica a un navegador que ya entro bien alguna vez."""
    ident = secrets.token_urlsafe(12)
    return f"{ident}.{_firma(secreto, f'dispositivo:{ident}')}"


def dispositivo_valido(secreto: str, cookie: str | None) -> str | None:
    """Devuelve el identificador del dispositivo, o None si la cookie no es nuestra."""
    if not cookie or not secreto:
        return None
    ident, _, firma = cookie.partition(".")
    if ident and hmac.compare_digest(firma.encode(), _firma(secreto, f"dispositivo:{ident}").encode()):
        return ident
    return None


class LimiteDeIntentos:
    """Bloquea el login tras demasiados fallos en una ventana de tiempo.

    No usa la IP (quien hace el pedido puede mentirla). Hay dos grupos:
      - cada dispositivo conocido (ya entro bien alguna vez) tiene su propia cuenta;
      - todos los desconocidos comparten una sola.
    Asi un extraño que prueba claves no puede dejar afuera al dueño en su celular o su PC.
    """

    def __init__(self, por_dispositivo: int = 5, desconocidos: int = 30, ventana_seg: int = 15 * 60):
        self.por_dispositivo, self.desconocidos, self.ventana = por_dispositivo, desconocidos, ventana_seg
        self._fallos: list[tuple[float, str | None]] = []

    def bloqueado(self, dispositivo: str | None) -> bool:
        limite = time.time() - self.ventana
        self._fallos = [f for f in self._fallos if f[0] > limite]
        fallos = sum(1 for _, d in self._fallos if d == dispositivo)
        return fallos >= (self.por_dispositivo if dispositivo else self.desconocidos)

    def registrar_fallo(self, dispositivo: str | None) -> None:
        self._fallos.append((time.time(), dispositivo))
