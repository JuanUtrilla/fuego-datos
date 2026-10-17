"""ICON-EU del servicio meteorológico alemán (DWD), CC BY 4.0.

Malla regular de 0,0625° (~7 km) sobre Europa. Pasadas cada 3 horas: las de
00, 06, 12 y 18 UTC llegan a 120 h (cada hora hasta 78 h, luego cada 3 h) y
las intermedias a 48 h. El DWD solo guarda la última pasada de cada hora, es
decir, un día: lo que no se baje en 24 horas se pierde.

Cada variable y paso es un fichero GRIB2 comprimido con bz2 en
`<pasada>/<variable>/`. Se listan las carpetas, se bajan todos los ficheros de
la pasada y cada campo se recorta al dominio.
"""
import bz2
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from .. import rejilla
from ..red import get

log = logging.getLogger(__name__)

BASE = "https://opendata.dwd.de/weather/nwp/icon-eu/grib"
HORAS = tuple(range(0, 24, 3))
LICENCIA = "CC BY 4.0. Fuente: Deutscher Wetterdienst (DWD). Datos recortados al dominio, sin otra modificación"

# variable de superficie → se guarda con su nombre en minúsculas
SUPERFICIE = (
    "t_2m", "td_2m", "relhum_2m", "tmax_2m", "tmin_2m", "t_g",     # temperatura y humedad
    "u_10m", "v_10m", "vmax_10m",                                  # viento y racha
    "tot_prec",                                                    # precipitación acumulada
    "aswdir_s", "aswdifd_s", "athb_s",                             # radiación
    "clct", "cape_ml", "lpi_con_max",                              # nubes, inestabilidad, rayos
    "pmsl", "ps", "mh",                                            # presión y altura de mezcla
)
# variable de suelo → profundidades (cm) que se guardan
SUELO = {"w_so": (0, 1, 3, 9)}

PATRON = re.compile(r'href="(icon-eu_europe_regular-lat-lon_(single|soil)-level_'
                    r'(\d{10})_(\d{3})(?:_(\d+))?_([A-Z0-9_]+)\.grib2\.bz2)"')


def listar(hora, variable):
    """Ficheros de una carpeta: [(nombre, pasada, paso, nivel)]."""
    html = get(f"{BASE}/{hora:02d}/{variable}/").text
    out = []
    for nombre, _, pasada, paso, nivel, _ in PATRON.findall(html):
        out.append((nombre, datetime.strptime(pasada, "%Y%m%d%H").replace(tzinfo=timezone.utc),
                    int(paso), int(nivel) if nivel else None))
    return out


def _bajar(url, variable, nivel):
    datos = bz2.decompress(get(url).content)
    campos = [rejilla.leer_campo(m, nombre=variable) for m in rejilla.separar_mensajes(datos)]
    for c in campos:
        if nivel is not None:
            c["nivel_tipo"], c["nivel"] = "soil", nivel
        else:
            c["nivel_tipo"] = "surface"
    return campos


def bajar_pasada(hora, pasada, listados):
    tareas = []
    for variable, ficheros in listados.items():
        niveles = SUELO.get(variable)
        for nombre, p, _, nivel in ficheros:
            if p != pasada or (niveles and nivel not in niveles):
                continue
            tareas.append((f"{BASE}/{hora:02d}/{variable}/{nombre}", variable, nivel))
    campos = []
    with ThreadPoolExecutor(16) as ex:
        for f in as_completed([ex.submit(_bajar, *t) for t in tareas]):
            campos += f.result()
    log.info("ICON-EU %s: %d campos de %d ficheros", f"{pasada:%F %H}z", len(campos), len(tareas))
    return rejilla.a_netcdf(campos, {
        "fuente": "DWD ICON-EU", "resolucion": "0.0625 grados",
        "licencia": LICENCIA, "origen": f"{BASE}/{hora:02d}/"})


def ejecutar(lote):
    """Para cada hora de pasada, baja la que esté publicada si está completa y
    no se ha guardado ya."""
    hechas = 0
    for hora in HORAS:
        try:
            ref = listar(hora, "t_2m")
        except Exception as e:
            log.warning("ICON-EU %02dz: no se pudo listar (%s)", hora, e)
            continue
        if not ref:
            continue
        pasada = max(p for _, p, _, _ in ref)
        pasos = sorted(s for _, p, s, _ in ref if p == pasada)
        clave = lote.clave("icon_eu", f"icon_eu_{pasada:%Y%m%d%H}.nc", pasada)
        if lote.almacen.existe(clave):
            continue
        # completa: el último paso esperado ya está, o la pasada tiene más de
        # 6 horas (el DWD termina de publicar en unas 4)
        esperado = 120 if hora % 6 == 0 else 48
        vieja = datetime.now(timezone.utc) - pasada > timedelta(hours=6)
        if pasos[-1] < esperado and not vieja:
            log.info("ICON-EU %s: incompleta (último paso %d de %d)", f"{pasada:%F %H}z", pasos[-1], esperado)
            continue
        listados = {"t_2m": ref}
        for v in (*SUPERFICIE, *SUELO):
            if v != "t_2m":
                listados[v] = listar(hora, v)
        datos = bajar_pasada(hora, pasada, listados)
        lote.guardar(clave, datos, origen=f"DWD ICON-EU {pasada:%Y%m%d%H}", licencia=LICENCIA)
        hechas += 1
    return hechas
