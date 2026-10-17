"""Dominio, rutas y variables de entorno.

El dominio es un rectángulo que cubre la península, Baleares y Portugal con un
poco de margen. Las mallas se guardan enteras dentro del rectángulo (mar y
países vecinos incluidos): recortar a tierra o a un país es trabajo del
cálculo, no de la descarga.
"""
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(RAIZ / ".env")
except ImportError:  # en GitHub las claves llegan como variables de entorno
    pass

NORTE, SUR, OESTE, ESTE = 44.2, 35.6, -10.0, 4.6

USER_AGENT = "fuego-datos/0.1 (descarga de datos abiertos; github.com/JuanUtrilla)"


def var(nombre, defecto=None, obligatoria=False):
    v = os.environ.get(nombre, "").strip() or defecto
    if obligatoria and not v:
        raise SystemExit(f"Falta la variable de entorno {nombre} (ver .env.ejemplo)")
    return v
