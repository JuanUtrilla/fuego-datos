"""IFS de ECMWF, datos abiertos (CC BY 4.0).

Se baja de la copia de ECMWF en AWS, que ECMWF recomienda frente a su portal
(limitado a 500 conexiones a la vez) y que conserva las pasadas desde 2023;
el portal solo guarda las 12 últimas.

Cada paso de cada pasada es un GRIB global con todos los campos y un `.index`
(una línea JSON por campo, con su posición y tamaño). Se lee el índice y se
piden por rangos de bytes solo los campos elegidos: unos 30 de los ~200 que
trae cada paso.

Pasadas de 00, 06, 12 y 18 UTC, de 0 a 144 h cada 3 h (las de 06 y 18 UTC,
hasta 90 h antes de 2025). Se guarda siempre la malla de 0,25°, que es con la
que está calibrado el cálculo de la web, y además la más fina que haya (0,1°
desde el 21/10/2026). En 0,1° cada paso viene en dos ficheros, superficie y
suelo (`-sfc`) y niveles de presión (`-pl`); ver `partes`.

En 0,25°, las de 00 y 12 UTC se bajan enteras, hasta 360 h (15 días): de 144 h
en adelante, cada 6 h. Así se puede dar la previsión de más días. Antes de la
pasada del 12/11/2024 12z llegaban a 240 h. Pasadas las 144 h, las máximas y
mínimas de temperatura son de 6 h (`mx2t6`, `mn2t6`) y la racha `10fg` es la
de las 6 h previas, no la de la última hora. La malla fina se queda en 144 h.

Hasta el 11/05/2026, las pasadas de 06 y 18 UTC están en la carpeta `scda`
(«short cut-off») y no en `oper`. `rellenar` baja pasadas antiguas del archivo
de AWS, que llega hasta el 18/01/2023.
"""
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

from .. import rejilla
from ..red import SESION, TIEMPO, get

log = logging.getLogger(__name__)

BASE = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"
HORAS = (0, 6, 12, 18)
PASO_MAX = 144
PASO_MAX_CORTO = 90      # 06 y 18 UTC antes de 2025
PASO_LARGO = 360         # 00 y 12 UTC en 0,25°
PASO_LARGO_ANTES = 240   # 00 y 12 UTC antes de INICIO_360
INICIO_360 = datetime(2024, 11, 12, 12)
ESPERA_LARGO = timedelta(hours=12)  # si a las 12 h no ha llegado a 360 h, se guarda lo que haya
MALLA_BASE = "0p25"      # la de la calibración: se guarda siempre
FIN_SCDA = date(2026, 5, 12)
HUECO = 1 << 20          # 1 MB
HILOS = 4
LICENCIA = "CC BY 4.0. © ECMWF. Datos recortados al dominio, sin otra modificación"

# (param, levtype, niveles). Nivel None = superficie.
CAMPOS = [
    *[(p, "sfc", None) for p in (
        "2t", "2d", "skt", "mx2t3", "mn2t3",          # temperaturas
        "mx2t6", "mn2t6",                              # máx. y mín. de 6 h, después de 144 h
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


def elegidos(lineas):
    """(posición, tamaño) de los campos elegidos, ordenados."""
    return sorted((int(x["_offset"]), int(x["_length"])) for x in lineas if _quiero(x))


def rangos(campos, hueco=HUECO):
    """Agrupa los campos en pocos rangos de bytes. Dos campos van en el mismo
    rango si entre ellos hay menos de `hueco` bytes de otros campos: se bajan
    unos bytes de más a cambio de muchas menos peticiones (la copia de AWS
    contesta «503 Slow Down» si se le hacen demasiadas)."""
    juntos = []
    for ini, largo in campos:
        if juntos and ini - (juntos[-1][0] + juntos[-1][1]) <= hueco:
            juntos[-1][1] = ini + largo - juntos[-1][0]
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


def flujo(fecha, hora):
    """Carpeta de la pasada: `scda` para 06 y 18 UTC antes del 12/05/2026."""
    dia = fecha.date() if isinstance(fecha, datetime) else fecha
    return "scda" if hora in (6, 18) and dia < FIN_SCDA else "oper"


def _url(fecha, hora, resol, paso, ext, parte=None):
    f = flujo(fecha, hora)
    return (f"{BASE}/{fecha:%Y%m%d}/{hora:02d}z/ifs/{resol}/{f}/"
            f"{fecha:%Y%m%d}{hora:02d}0000-{paso}h-{f}-fc{'-' + parte if parte else ''}.{ext}")


def partes(fecha, hora, resol):
    """Ficheros de cada paso. En 0,25° es uno (`…-fc.grib2`); en 0,1° son dos,
    superficie y suelo (`…-fc-sfc.grib2`) y niveles de presión (`…-fc-pl.grib2`).
    Se mira en el paso 0. Vacío si la pasada aún no ha empezado a publicarse."""
    clave = (f"{fecha:%Y%m%d}", hora, resol)
    if clave not in _PARTES:
        f = flujo(fecha, hora)
        r = get(BASE, params={"list-type": "2", "prefix": (
            f"{fecha:%Y%m%d}/{hora:02d}z/ifs/{resol}/{f}/{fecha:%Y%m%d}{hora:02d}0000-0h-{f}-fc")})
        sufijos = set(re.findall(r"-0h-[a-z]+-fc(?:-([a-z]+))?\.index</Key>", r.text))
        if not sufijos:
            return ()
        _PARTES[clave] = (None,) if "" in sufijos else tuple(sorted(sufijos))
    return _PARTES[clave]


_PARTES = {}    # (fecha, hora, malla) → partes; no cambia una vez publicada la pasada


def _existe(fecha, hora, resol, paso):
    ps = partes(fecha, hora, resol)
    return bool(ps) and all(
        SESION.head(_url(fecha, hora, resol, paso, "index", p), timeout=TIEMPO).status_code == 200
        for p in ps)


def paso_largo(fecha, hora, resol):
    """Último paso de una pasada larga (00/12 UTC en la malla base), o None."""
    if hora not in (0, 12) or resol != MALLA_BASE:
        return None
    ini = datetime(fecha.year, fecha.month, fecha.day, hora)
    return PASO_LARGO if ini >= INICIO_360 else PASO_LARGO_ANTES


def pasos(paso_max):
    """Cada 3 h hasta 144 h y cada 6 h desde ahí, como los publica ECMWF."""
    return list(range(0, min(paso_max, PASO_MAX) + 1, 3)) + list(range(PASO_MAX + 6, paso_max + 1, 6))


def ultimo_paso(fecha, hora, resol, ahora=None):
    """Hasta dónde bajar la pasada, o None si aún no está completa.

    Las largas (00/12 UTC, 0,25°) se esperan hasta su último paso, que ECMWF
    publica después que el de 144 h. Si a las ESPERA_LARGO de la pasada no ha
    aparecido, o es una antigua del archivo que no lo tiene, se queda en 144 h.
    Las demás: 144 h, o 90 h si es una de 06/18 UTC antigua."""
    largo = paso_largo(fecha, hora, resol)
    if largo and _existe(fecha, hora, resol, largo):
        return largo
    if _existe(fecha, hora, resol, PASO_MAX):
        if largo:
            ahora = ahora or datetime.now(timezone.utc)
            ini = datetime(fecha.year, fecha.month, fecha.day, hora, tzinfo=timezone.utc)
            if ahora - ini < ESPERA_LARGO:
                return None
            log.warning("IFS %s %02dz %s: sin el paso de %d h; se guarda hasta %d h",
                        f"{fecha:%F}", hora, resol, largo, PASO_MAX)
        return PASO_MAX
    if flujo(fecha, hora) == "scda" and _existe(fecha, hora, resol, PASO_MAX_CORTO):
        return PASO_MAX_CORTO
    return None


def _bajar_parte(fecha, hora, resol, paso, parte):
    url = _url(fecha, hora, resol, paso, "grib2", parte)
    lineas = [json.loads(l) for l in get(_url(fecha, hora, resol, paso, "index", parte)).text.splitlines()
              if l.strip()]
    campos = elegidos(lineas)
    trozos = []
    for ini, largo in rangos(campos):
        r = get(url, headers={"Range": f"bytes={ini}-{ini + largo - 1}"})
        if len(r.content) != largo:
            raise IOError(f"paso {paso}: se esperaban {largo} bytes y llegaron {len(r.content)}")
        # del rango se recortan solo los campos elegidos
        trozos += [r.content[o - ini:o - ini + n] for o, n in campos if ini <= o < ini + largo]
    if len(trozos) != len(campos):
        raise IOError(f"paso {paso}: {len(trozos)} campos de {len(campos)}")
    return b"".join(trozos)


def _bajar_paso(fecha, hora, resol, paso):
    return paso, b"".join(_bajar_parte(fecha, hora, resol, paso, p) for p in partes(fecha, hora, resol))


def bajar_pasada(fecha, hora, resol, paso_max=PASO_MAX):
    lista = pasos(paso_max)
    campos = []
    # Se descargan HILOS pasos a la vez y cada uno se recorta en cuanto llega, para
    # no tener en memoria los campos globales de todos los pasos.
    with ThreadPoolExecutor(HILOS) as ex:
        futuros = [ex.submit(_bajar_paso, fecha, hora, resol, p) for p in lista]
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
    log.info("IFS %s %02dz %s: %d campos de %d pasos", f"{fecha:%F}", hora, resol, len(campos), len(lista))
    return rejilla.a_netcdf(campos, {
        "fuente": "ECMWF IFS HRES, datos abiertos", "resolucion": resol,
        "licencia": LICENCIA,
        "origen": f"{BASE}/{fecha:%Y%m%d}/{hora:02d}z/ifs/{resol}/{flujo(fecha, hora)}/"})


def _una(lote, fecha, hora, resol):
    """Baja y guarda una pasada si no está ya. True si la guarda, False si aún
    no está completa o ya estaba; lanza la excepción si falla la descarga."""
    nombre = f"ifs_{resol}_{fecha:%Y%m%d}{hora:02d}.nc"
    clave = lote.clave(f"ifs_{resol}", nombre, fecha)
    if lote.almacen.existe(clave):
        return False
    hasta = ultimo_paso(fecha, hora, resol)
    if hasta is None:
        log.info("IFS %s %02dz %s: aún incompleta", f"{fecha:%F}", hora, resol)
        return False
    datos = bajar_pasada(fecha, hora, resol, hasta)
    lote.guardar(clave, datos, origen=f"ECMWF IFS {resol} {fecha:%Y%m%d} {hora:02d}z",
                 licencia=LICENCIA)
    return True


def _vuelta(lote, pasadas, mallas):
    """Una vuelta sobre `pasadas`. Devuelve las guardadas y las que fallaron,
    como (fecha, hora, texto para el log)."""
    hechas, fallidas = 0, []
    for fecha, hora in pasadas:
        try:
            resols = resoluciones(fecha, hora)
        except Exception as e:
            log.warning("IFS %s %02dz: no se pudo listar (%s)", f"{fecha:%F}", hora, e)
            fallidas.append((fecha, hora, f"{fecha:%F} {hora:02d}z"))
            continue
        for resol in mallas(resols):
            try:
                hechas += _una(lote, fecha, hora, resol)
            except Exception as e:
                log.warning("IFS %s %02dz %s: falló (%s); sigo con la siguiente",
                            f"{fecha:%F}", hora, resol, e)
                fallidas.append((fecha, hora, f"{fecha:%F} {hora:02d}z {resol}"))
    return hechas, fallidas


def _recorrer(lote, pasadas, mallas, esperas=()):
    """Baja cada (fecha, hora) en las mallas que dé `mallas(resols)`. Si una
    pasada falla, se sigue con las demás. Al acabar se vuelve a por las
    fallidas tras cada pausa de `esperas` (s): el «503 Slow Down» de S3 dura a
    veces más que los reintentos de cada petición, pero no media hora. Lo ya
    guardado se salta, así que repetir una pasada solo baja lo que falta."""
    hechas, fallidas = _vuelta(lote, pasadas, mallas)
    for espera in esperas:
        if not fallidas:
            break
        log.info("IFS: %d pasada(s) fallidas; vuelvo a por ellas en %d min",
                 len(fallidas), espera // 60)
        time.sleep(espera)
        n, fallidas = _vuelta(lote, list(dict.fromkeys((f, h) for f, h, _ in fallidas)), mallas)
        hechas += n
    if fallidas:
        raise RuntimeError(f"IFS: {hechas} pasada(s) guardada(s), fallaron "
                           f"{', '.join(t for _, _, t in fallidas)}")
    return hechas


def ejecutar(lote, dias=2):
    """Baja las pasadas de los últimos `dias` días que estén completas y aún no
    se hayan guardado, en 0,25° y en la malla más fina si la hay. Así un job
    retrasado o caído se recupera solo."""
    ahora = datetime.now(timezone.utc)
    hoy = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    pasadas = [(hoy - timedelta(days=d), h) for d in range(dias, -1, -1) for h in HORAS
               if hoy - timedelta(days=d) + timedelta(hours=h) <= ahora]
    # Una sola vuelta extra: el job corre cada 3 h y recupera los dos últimos días.
    return _recorrer(lote, pasadas, lambda r: list(dict.fromkeys(
        [m for m in (MALLA_BASE, r[0] if r else None) if m in r])), esperas=(300,))


def rellenar(lote, desde, hasta, malla=MALLA_BASE):
    """Baja del archivo de AWS las pasadas de una malla (0,25° si no se dice
    otra) entre dos fechas (incluidas) que no estén ya en el almacén."""
    d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
    pasadas = [(datetime(d.year, d.month, d.day, tzinfo=timezone.utc), h)
               for d in (d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)) for h in HORAS]
    log.info("IFS relleno %s %s → %s: %d pasadas", malla, desde, hasta, len(pasadas))
    return _recorrer(lote, pasadas, lambda r: [malla] if malla in r else [],
                     esperas=(300, 900, 1800))
