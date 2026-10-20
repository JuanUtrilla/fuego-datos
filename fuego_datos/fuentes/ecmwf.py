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
que está calibrado el cálculo de la web, y además la más fina que haya: ECMWF
ha anunciado 0,1° para finales de octubre de 2026 y entrará sola en cuanto
aparezca la carpeta.

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
MALLA_BASE = "0p25"      # la de la calibración: se guarda siempre
FIN_SCDA = date(2026, 5, 12)
HUECO = 1 << 20          # 1 MB
HILOS = 4
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


def _url(fecha, hora, resol, paso, ext):
    f = flujo(fecha, hora)
    return (f"{BASE}/{fecha:%Y%m%d}/{hora:02d}z/ifs/{resol}/{f}/"
            f"{fecha:%Y%m%d}{hora:02d}0000-{paso}h-{f}-fc.{ext}")


def _existe(fecha, hora, resol, paso):
    return SESION.head(_url(fecha, hora, resol, paso, "index"), timeout=TIEMPO).status_code == 200


def ultimo_paso(fecha, hora, resol):
    """144 h si la pasada está completa; 90 h si es una de 06/18 UTC antigua que
    solo llega ahí; None si aún no está completa."""
    if _existe(fecha, hora, resol, PASO_MAX):
        return PASO_MAX
    if flujo(fecha, hora) == "scda" and _existe(fecha, hora, resol, PASO_MAX_CORTO):
        return PASO_MAX_CORTO
    return None


def _bajar_paso(fecha, hora, resol, paso):
    url = _url(fecha, hora, resol, paso, "grib2")
    lineas = [json.loads(l) for l in get(_url(fecha, hora, resol, paso, "index")).text.splitlines() if l.strip()]
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
    return paso, b"".join(trozos)


def bajar_pasada(fecha, hora, resol, paso_max=PASO_MAX):
    pasos = list(range(0, paso_max + 1, 3))
    campos = []
    # Se descargan HILOS pasos a la vez y cada uno se recorta en cuanto llega, para
    # no tener en memoria los campos globales de todos los pasos.
    with ThreadPoolExecutor(HILOS) as ex:
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


def rellenar(lote, desde, hasta):
    """Baja del archivo de AWS las pasadas de 0,25° entre dos fechas (incluidas)
    que no estén ya en el almacén."""
    d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
    pasadas = [(datetime(d.year, d.month, d.day, tzinfo=timezone.utc), h)
               for d in (d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)) for h in HORAS]
    log.info("IFS relleno %s → %s: %d pasadas", desde, hasta, len(pasadas))
    return _recorrer(lote, pasadas, lambda r: [MALLA_BASE] if MALLA_BASE in r else [],
                     esperas=(300, 900, 1800))
