"""Peticiones HTTP con reintentos.

Todas las descargas pasan por aquí: hasta 8 reintentos con espera creciente
(unos 6 minutos en total) ante errores de red, 429 y 5xx, respetando la
cabecera Retry-After, y un tiempo máximo por petición para que un servidor
colgado no bloquee el job.
"""
import json
import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import USER_AGENT

log = logging.getLogger(__name__)

TIEMPO = (20, 300)  # conexión, lectura (s)


def sesion(intentos=8, hilos=16):
    s = requests.Session()
    reintentos = Retry(
        total=intentos, connect=intentos, read=intentos, status=intentos,
        backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"), respect_retry_after_header=True,
        raise_on_status=False)
    adaptador = HTTPAdapter(max_retries=reintentos, pool_connections=hilos, pool_maxsize=hilos)
    s.mount("https://", adaptador)
    s.mount("http://", adaptador)
    s.headers["User-Agent"] = USER_AGENT
    return s


SESION = sesion()


def get(url, **kw):
    kw.setdefault("timeout", TIEMPO)
    r = SESION.get(url, **kw)
    r.raise_for_status()
    return r


class SinDatos(Exception):
    """La fuente responde bien pero no tiene ese producto ahora mismo."""


AEMET = "https://opendata.aemet.es/opendata/api"


def aemet(ruta, clave, intentos=4):
    """Petición a AEMET OpenData, que va en dos pasos.

    La primera llamada devuelve un JSON con la URL de los datos (`datos`); la
    segunda, los datos. AEMET contesta 429 cuando se pasa del límite por
    minuto, a veces dentro del JSON con HTTP 200, así que se mira `estado`.
    """
    for intento in range(1, intentos + 1):
        r = SESION.get(f"{AEMET}/{ruta.lstrip('/')}", headers={"api_key": clave},
                       timeout=TIEMPO)
        try:
            sobre = r.json()
        except json.JSONDecodeError:
            sobre = {"estado": r.status_code, "descripcion": r.text[:200]}
        estado = int(sobre.get("estado", r.status_code))
        if estado == 200 and sobre.get("datos"):
            return get(sobre["datos"]).content
        if estado == 404:
            raise SinDatos(f"AEMET {ruta}: {sobre.get('descripcion')}")
        if estado == 401:
            raise SystemExit("AEMET: clave rechazada (401). Revisa AEMET_API_KEY")
        espera = 65 * intento if estado == 429 else 10 * intento
        log.warning("AEMET %s: estado %s (%s); reintento en %s s", ruta, estado,
                    sobre.get("descripcion"), espera)
        time.sleep(espera)
    raise RuntimeError(f"AEMET {ruta}: sin respuesta válida tras {intentos} intentos")
