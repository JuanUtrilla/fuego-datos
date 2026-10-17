"""AEMET OpenData. Reutilización libre, también comercial, citando a AEMET.

Nada de esto tiene archivo en AEMET: lo que no se guarda cuando está publicado
se pierde. Por eso se guarda tal como llega, sin transformar.

- observacion: todas las estaciones, últimas 12 horas. Cada 3 horas, con
  solape, para no perder ninguna hora aunque GitHub retrase el job.
- prediccion: horaria (48 h) y diaria (7 días) de los 8.124 municipios, en un
  tar.gz cada una.
- productos: mapas de riesgo meteorológico de incendio (de mañana a +7 días y
  el estimado de hoy), avisos de fenómenos adversos (CAP) y HARMONIE-AROME.
"""
import json
import logging
import re

from ..almacen import comprimir
from ..config import var
from ..red import SinDatos, aemet, get

log = logging.getLogger(__name__)

LICENCIA = "Fuente: AEMET. Reutilización conforme a la Ley 37/2007 y a la nota legal de AEMET"
HARMONIE = "https://www.aemet.es/es/api-eltiempo/modelos/download/harmonie/PB"


def _clave():
    return var("AEMET_API_KEY", obligatoria=True)


def _hora(lote):
    return f"{lote.inicio:%H%M%S}"


def observacion(lote):
    ruta = "observacion/convencional/todas"
    datos = aemet(ruta, _clave())
    filas = json.loads(datos.decode("latin-1"))
    ultima = re.sub(r"\D", "", max(f["fint"] for f in filas))[:10]     # aaaammddhh
    log.info("AEMET observación: %d registros de %d estaciones, hasta %s",
             len(filas), len({f['idema'] for f in filas}), ultima)
    clave = lote.clave("observacion", f"observacion_{ultima}.json.gz")
    if lote.almacen.existe(clave):
        log.info("AEMET observación hasta %s: ya guardada", ultima)
        return 0
    lote.guardar(clave, comprimir(datos), origen=f"AEMET OpenData /{ruta}", licencia=LICENCIA)
    return 1


def nombre_gzip(datos: bytes) -> str:
    """Nombre original guardado en la cabecera gzip (campo FNAME, RFC 1952).
    El de AEMET empieza por un sello de tiempo: `1792242605_municipios_h_json.tar`."""
    if datos[:2] != b"\x1f\x8b":
        return ""
    banderas, pos = datos[3], 10
    if banderas & 0x04:                       # FEXTRA
        pos += 2 + int.from_bytes(datos[10:12], "little")
    if not banderas & 0x08:                   # sin FNAME
        return ""
    return datos[pos:datos.index(b"\0", pos)].decode("latin-1")


def _tar(lote, producto, ruta):
    datos = aemet(ruta, _clave())
    if datos[:2] != b"\x1f\x8b":
        raise RuntimeError(f"AEMET {producto}: se esperaba un tar.gz")
    sello = re.match(r"(\d+)", nombre_gzip(datos))
    sufijo = sello.group(1) if sello else _hora(lote)
    clave = lote.clave(producto, f"{producto}_{sufijo}.tar.gz")
    return lote.guardar_si_cambia(f"aemet_{producto}", clave, datos,
                                  origen=f"AEMET OpenData /{ruta}", licencia=LICENCIA)


def prediccion(lote):
    n = _tar(lote, "prediccion_horaria", "prediccion/especifica/municipio/horaria/todos")
    n += _tar(lote, "prediccion_diaria", "prediccion/especifica/municipio/diaria/todos")
    return n


def _preparar(datos):
    """Extensión según el contenido. Los tar sin comprimir (los avisos CAP)
    se guardan comprimidos."""
    if datos[257:262] == b"ustar":
        return comprimir(datos), "tar.gz"
    for firma, ext in ((b"\x89PNG", "png"), (b"GIF8", "gif"), (b"\x1f\x8b", "tar.gz"),
                       (b"<?xml", "xml"), (b"%PDF", "pdf")):
        if datos.startswith(firma):
            return datos, ext
    return datos, "bin"


def productos(lote):
    n = 0
    clave_api = _clave()
    for dia in range(1, 8):
        ruta = f"incendios/mapasriesgo/previsto/dia/{dia}/area/p"
        try:
            datos = aemet(ruta, clave_api)
        except SinDatos as e:
            log.info("%s", e)
            continue
        datos, ext = _preparar(datos)
        clave = lote.clave("incendio_previsto", f"incendio_previsto_d{dia}_{_hora(lote)}.{ext}")
        n += lote.guardar_si_cambia(f"aemet_incendio_previsto_d{dia}", clave, datos,
                                    origen=f"AEMET OpenData /{ruta}", licencia=LICENCIA)
    for ruta, producto in (("incendios/mapasriesgo/estimado/area/p", "incendio_estimado"),
                           ("avisos_cap/ultimoelaborado/area/esp", "avisos")):
        try:
            datos = aemet(ruta, clave_api)
        except SinDatos as e:
            log.info("%s", e)
            continue
        datos, ext = _preparar(datos)
        clave = lote.clave(producto, f"{producto}_{_hora(lote)}.{ext}")
        n += lote.guardar_si_cambia(f"aemet_{producto}", clave, datos,
                                    origen=f"AEMET OpenData /{ruta}", licencia=LICENCIA)
    r = get(HARMONIE)
    nombre = re.search(r"filename=([^;]+)", r.headers.get("content-disposition", ""))
    sello = re.search(r"(\d+)", nombre.group(1)) if nombre else None
    clave = lote.clave("harmonie", f"harmonie_pb_{sello.group(1) if sello else _hora(lote)}.tar.gz")
    n += lote.guardar_si_cambia("aemet_harmonie", clave, r.content, origen=HARMONIE,
                                licencia="CC BY 4.0. Fuente: AEMET")
    return n
