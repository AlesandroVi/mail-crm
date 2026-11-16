"""Define (o cambia) la contraseña del panel.

Uso:  python -m mail_crm.clave

La contraseña se escribe en la terminal y no se muestra. En .env se guarda solo
su hash (no la contraseña) y el secreto que firma las sesiones.
Cambiarla cierra todas las sesiones abiertas.
"""
import os
import sys
from getpass import getpass

from . import auth, config

RUTA_ENV = config.RAIZ / ".env"


def guardar_en_env(valores: dict[str, str]) -> None:
    """Agrega o reemplaza claves en .env sin tocar el resto de las lineas."""
    lineas = RUTA_ENV.read_text(encoding="utf-8").splitlines() if RUTA_ENV.exists() else []
    lineas = [l for l in lineas if l.split("=", 1)[0].strip() not in valores]
    lineas += [f"{k}={v}" for k, v in valores.items()]
    RUTA_ENV.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def leer_clave(pregunta: str) -> str:
    """Pide la contraseña mostrando un * por cada tecla, para que se vea que escribe."""
    en_consola = os.name == "nt" and sys.stdin.isatty() and sys.stdout.isatty()
    if not en_consola:
        return getpass(pregunta)  # fuera de una consola de Windows: sin asteriscos
    import msvcrt

    print(pregunta, end="", flush=True)
    letras: list[str] = []
    while True:
        tecla = msvcrt.getwch()
        if tecla in ("\r", "\n"):
            print()
            return "".join(letras)
        if tecla == "\x03":  # Ctrl+C
            raise KeyboardInterrupt
        if tecla == "\b":
            if letras:
                letras.pop()
                print("\b \b", end="", flush=True)
        elif tecla in ("\x00", "\xe0"):
            msvcrt.getwch()  # flechas y teclas especiales: se ignoran
        elif tecla.isprintable():
            letras.append(tecla)
            print("*", end="", flush=True)


def main() -> None:
    print(f"Elegi una contraseña para el panel (minimo {auth.LARGO_MINIMO} caracteres).")
    print("No uses una que ya uses en otro lado. Vas a ver un * por cada tecla.\n")
    clave = leer_clave("Contraseña nueva: ")
    if len(clave) < auth.LARGO_MINIMO:
        raise SystemExit(f"Muy corta: tiene que tener al menos {auth.LARGO_MINIMO} caracteres.")
    if leer_clave("Repetila: ") != clave:
        raise SystemExit("No coinciden. Volve a correr el comando.")
    guardar_en_env({"PANEL_CLAVE_HASH": auth.hash_clave(clave), "PANEL_SECRETO": auth.nuevo_secreto()})
    print("\nListo. La proxima vez que abras el panel te va a pedir la contraseña.")
    print("Si el panel estaba abierto, cerralo y volve a abrirlo.")


if __name__ == "__main__":
    main()
