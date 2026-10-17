"""IFS de ECMWF, datos abiertos (CC BY 4.0, uso comercial permitido).

Se baja de la copia de ECMWF en AWS, que ECMWF recomienda frente a su portal
(limitado a 500 conexiones a la vez) y que conserva las pasadas desde 2023;
el portal solo guarda las 12 últimas.

Cada paso de cada pasada es un GRIB global con todos los campos y un `.index`
(una línea JSON por campo, con su posición y tamaño). Se lee el índice y se
piden por rangos de bytes solo los campos elegidos: unos 30 de los ~200 que
trae cada paso.

Pasadas de 00, 06, 12 y 18 UTC, de 0 a 144 h cada 3 h. Se usa la malla más
fina que haya publicada: hoy 0,25°; ECMWF ha anunciado 0,1° para finales de
octubre de 2026 y entrará sola en cuanto aparezca la carpeta.
"""
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from .. import rejilla
from ..red import SESION, TIEMPO, get

log = logging.getLogger(__name__)

BASE = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"
HORAS = (0, 6, 12, 18)
PASO_MAX = 144
LICENCIA = "CC BY 4.0. © ECMWF. Datos recortados al dominio, sin otra modificación"

# (param, levtype, niveles). Nivel None = superficie.
CAMPOS = [
    *[(p, "sfc", None) for p in (
        "2t", "2d", "skt", "mx2t3", "mn2t3",          # temperaturas
        "10u", "10v", "100u", "100v",                 # viento
        "10fg", "10fg3",                               # racha: hasta 90 h horaria, luego trihoraria
        "tp", "ssrd", "strd",                          # precipitación y radiación (acumuladas)
        "sp", "msl", "tcc", "tcwv", "mucape")],
    ("vsw", "sol", (1, 2, 3, 4)),                      # humedad del suelo, 4 capas
    ("sot", "sol", (1,)),                              # temperatura del suelo, capa 1
    *[(p, "pl", (850, 700)) for p in ("t", "r", "u", "v")],  # índice de Haines y viento en altura
    ("gh", "pl", (500,)),
]


def _quiero(linea):
    for param, levtype, niveles in CAMPOS:
        if linea.get("param") == param and linea.get("levtype") == levtype:
            if niveles is None:
                return True
            return int(linea.get("levelist", -1)) in niveles
    return False


def rangos(lineas):
    """Rangos de bytes de los campos elegidos, uniendo los contiguos."""
    sel = sorted((int(x["_offset"]), int(x["_length"])) for x in lineas if _quiero(x))
    juntos = []
    for ini, largo in sel:
        if juntos and juntos[-1][0] + juntos[-1][1] == ini:
            juntos[-1][1] += largo
        else:
            juntos.append([ini, largo])
    return [tuple(r) for r in juntos]


def _resol_valor(nombre):
    m = re.fullmatch(r"0p(\d+)", nombre)
    return float("0." + m.group(1)) if m else None


def resoluciones(fecha, hora):
    """Resoluciones publicadas para una pasada, de la más fina a la más gruesa."""
    r = get(BASE, params={"list-type": "2", "prefix": f"{fecha:%Y%m%d}/{hora:02d}z/ifs/",
                          "delimiter": "/"})
    nombres = [p.rstrip("/").split("/")[-1] for p in re.findall(r"<Prefix>([^<]+)</Prefix>", r.text)]
    validas = [n for n in nombres if _resol_valor(n)]
    return sorted(validas, key=_resol_valor)


def _url(fecha, hora, resol, paso, ext):
    return (f"{BASE}/{fecha:%Y%m%d}/{hora:02d}z/ifs/{resol}/oper/"
            f"{fecha:%Y%m%d}{hora:02d}0000-{paso}h-oper-fc.{ext}")


def completa(fecha, hora, resol):
    r = SESION.head(_url(fecha, hora, resol, PASO_MAX, "index"), timeout=TIEMPO)
    return r.status_code == 200


def _bajar_paso(fecha, hora, resol, paso):
    lineas = [json.loads(l) for l in get(_url(fecha, hora, resol, paso, "index")).text.splitlines() if l.strip()]
    trozos = []
    for ini, largo in rangos(lineas):
        r = get(_url(fecha, hora, resol, paso, "grib2"),
                headers={"Range": f"bytes={ini}-{ini + largo - 1}"})
        if len(r.content) != largo:
            raise IOError(f"paso {paso}: se esperaban {largo} bytes y llegaron {len(r.content)}")
        trozos.append(r.content)
    return paso, b"".join(trozos)


def bajar_pasada(fecha, hora, resol):
    pasos = list(range(0, PASO_MAX + 1, 3))
    campos = []
    # Se descargan 8 pasos a la vez y cada uno se recorta en cuanto llega, para
    # no tener en memoria los campos globales de todos los pasos.
    with ThreadPoolExecutor(8) as ex:
        futuros = [ex.submit(_bajar_paso, fecha, hora, resol, p) for p in pasos]
        for f in as_completed(futuros):
            paso, bloque = f.result()
            # El fichero del paso 0 trae máximas y mínimas «de relleno» (todo
            # ceros) con el periodo 0-3 h, que chocarían con las del paso 3.
            # Cada campo se toma solo del fichero de su paso final. Y las
            # máximas o mínimas sobre un intervalo de 0 h (la racha del paso 0)
            # también son relleno.
            campos += [c for c in map(rejilla.leer_campo, rejilla.separar_mensajes(bloque))
                       if c["paso"] == paso
                       and not (c["tipo_paso"] in ("max", "min") and c["inicio"] == c["paso"])]
    log.info("IFS %s %02dz %s: %d campos de %d pasos", f"{fecha:%F}", hora, resol, len(campos), len(pasos))
    return rejilla.a_netcdf(campos, {
        "fuente": "ECMWF IFS HRES, datos abiertos", "resolucion": resol,
        "licencia": LICENCIA, "origen": f"{BASE}/{fecha:%Y%m%d}/{hora:02d}z/ifs/{resol}/oper/"})


def ejecutar(lote, dias=2):
    """Baja las pasadas de los últimos `dias` días que estén completas y aún no
    se hayan guardado. Así un job retrasado o caído se recupera solo."""
    hoy = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    hechas = 0
    for d in range(dias, -1, -1):
        fecha = hoy - timedelta(days=d)
        for hora in HORAS:
            if fecha + timedelta(hours=hora) > datetime.now(timezone.utc):
                continue
            try:
                resols = resoluciones(fecha, hora)
            except Exception as e:
                log.warning("IFS %s %02dz: no se pudo listar (%s)", f"{fecha:%F}", hora, e)
                continue
            if not resols:
                continue
            resol = resols[0]
            nombre = f"ifs_{resol}_{fecha:%Y%m%d}{hora:02d}.nc"
            clave = lote.clave(f"ifs_{resol}", nombre, fecha)
            if lote.almacen.existe(clave):
                continue
            if not completa(fecha, hora, resol):
                log.info("IFS %s %02dz %s: aún incompleta", f"{fecha:%F}", hora, resol)
                continue
            datos = bajar_pasada(fecha, hora, resol)
            lote.guardar(clave, datos, origen=f"ECMWF IFS {resol} {fecha:%Y%m%d} {hora:02d}z",
                         licencia=LICENCIA)
            hechas += 1
    return hechas
