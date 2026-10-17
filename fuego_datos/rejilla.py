"""GRIB → recorte al dominio → NetCDF comprimido.

Los modelos de ECMWF y del DWD publican campos globales o europeos. Guardarlos
enteros multiplicaría el volumen por cien, así que cada campo se recorta al
rectángulo del dominio (`config`) sin interpolar: se conservan los puntos de
la malla nativa tal cual.

El NetCDF lleva compresión zlib y una precisión fija por variable
(`least_significant_digit`, ver PRECISION).
"""
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .config import ESTE, NORTE, OESTE, SUR

log = logging.getLogger(__name__)


def separar_mensajes(datos: bytes):
    """Parte un bloque de bytes en mensajes GRIB (ediciones 1 y 2)."""
    pos, n = 0, len(datos)
    while pos < n:
        ini = datos.find(b"GRIB", pos)
        if ini < 0:
            break
        edicion = datos[ini + 7]
        if edicion == 2:
            largo = int.from_bytes(datos[ini + 8:ini + 16], "big")
        elif edicion == 1:
            largo = int.from_bytes(datos[ini + 4:ini + 7], "big")
        else:
            raise ValueError(f"GRIB de edición desconocida ({edicion}) en el byte {ini}")
        yield datos[ini:ini + largo]
        pos = ini + largo


def _indices_lon(lon0, di, ni):
    lons = lon0 + di * np.arange(ni)
    lons = (lons + 180.0) % 360.0 - 180.0
    sel = np.where((lons >= OESTE - 1e-6) & (lons <= ESTE + 1e-6))[0]
    orden = sel[np.argsort(lons[sel])]
    return orden, lons[orden]


def _indices_lat(lat0, dj, nj, sube):
    lats = lat0 + (dj if sube else -dj) * np.arange(nj)
    sel = np.where((lats >= SUR - 1e-6) & (lats <= NORTE + 1e-6))[0]
    orden = sel[np.argsort(-lats[sel])]          # de norte a sur
    return orden, lats[orden]


def leer_campo(mensaje: bytes, nombre=None):
    """Decodifica un mensaje GRIB en malla regular y lo recorta al dominio.

    Devuelve un dict con nombre, nivel, pasada, validez, lats, lons y valores
    (2D, norte arriba). `nombre` sustituye al shortName de eccodes, que para
    algunas variables locales del DWD es «unknown»."""
    import eccodes as ec
    gid = ec.codes_new_from_message(mensaje)
    try:
        tipo = ec.codes_get(gid, "gridType")
        if tipo != "regular_ll":
            raise ValueError(f"malla {tipo} no soportada")
        ni, nj = ec.codes_get(gid, "Ni"), ec.codes_get(gid, "Nj")
        lat0 = ec.codes_get_double(gid, "latitudeOfFirstGridPointInDegrees")
        lon0 = ec.codes_get_double(gid, "longitudeOfFirstGridPointInDegrees")
        di = ec.codes_get_double(gid, "iDirectionIncrementInDegrees")
        dj = ec.codes_get_double(gid, "jDirectionIncrementInDegrees")
        sube = bool(ec.codes_get(gid, "jScansPositively"))
        valores = ec.codes_get_values(gid).reshape(nj, ni)
        if ec.codes_get(gid, "bitmapPresent"):
            valores = np.where(valores == ec.codes_get_double(gid, "missingValue"), np.nan, valores)
        corto = nombre or ec.codes_get(gid, "shortName")
        nivel_tipo = ec.codes_get(gid, "typeOfLevel")
        nivel = ec.codes_get(gid, "level")
        fecha, hora = ec.codes_get(gid, "dataDate"), ec.codes_get(gid, "dataTime")
        # Los pasos se leen en minutos: algunos campos del DWD vienen en
        # minutos y el resto en horas
        ec.codes_set_string(gid, "stepUnits", "m")
        paso_min = ec.codes_get_long(gid, "endStep")
        inicio_min = ec.codes_get_long(gid, "startStep")
        tipo_paso = ec.codes_get(gid, "stepType")
        unidades = ec.codes_get(gid, "units") if ec.codes_is_defined(gid, "units") else ""
    finally:
        ec.codes_release(gid)
    pasada = datetime.strptime(f"{fecha}{hora:04d}", "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    ilon, lons = _indices_lon(lon0, di, ni)
    ilat, lats = _indices_lat(lat0, dj, nj, sube)
    return {"nombre": corto, "nivel_tipo": nivel_tipo, "nivel": nivel, "pasada": pasada,
            "paso": paso_min / 60, "inicio": inicio_min / 60, "tipo_paso": tipo_paso,
            "validez": pasada + timedelta(minutes=paso_min),
            "lats": lats, "lons": lons, "unidades": unidades,
            "valores": valores[np.ix_(ilat, ilon)].astype(np.float32)}


# Decimales que se guardan por variable (lo que no esté aquí, 2). Muy por
# debajo del error de cualquier modelo: 0,1 K, 0,1 m/s, 1 % de humedad,
# 0,01 mm de lluvia. Con esto el NetCDF ocupa casi la mitad que a precisión
# completa (medido con ICON-EU: 79 → 44 MB por pasada).
PRECISION = {
    # temperatura (K)
    "2t": 1, "2d": 1, "skt": 1, "t": 1, "sot": 1, "mx2t3": 1, "mn2t3": 1,
    "t_2m": 1, "td_2m": 1, "tmax_2m": 1, "tmin_2m": 1, "t_g": 1,
    # viento y racha (m/s)
    "10u": 1, "10v": 1, "100u": 1, "100v": 1, "10fg": 1, "10fg3": 1, "u": 1, "v": 1,
    "u_10m": 1, "v_10m": 1, "vmax_10m": 1,
    # precipitación: tp en m, tot_prec en kg/m² (= mm)
    "tp": 5, "tot_prec": 2,
    # radiación (J/m² acumulados, W/m² medios), presión (Pa), geopotencial (m²/s²)
    "ssrd": 0, "strd": 0, "aswdir_s": 0, "aswdifd_s": 0, "athb_s": 0,
    "sp": 0, "msl": 0, "pmsl": 0, "ps": 0, "gh": 0,
    # humedad (%), nubes (0-1 o %), agua precipitable (kg/m²)
    "r": 0, "relhum_2m": 0, "tcc": 2, "clct": 0, "tcwv": 1,
    # inestabilidad, rayos, altura de mezcla
    "mucape": 0, "cape_ml": 0, "lpi_con_max": 1, "mh": 0,
    # humedad del suelo: vsw en m³/m³, w_so en kg/m²
    "vsw": 3, "w_so": 1,
}


def precision(variable):
    """Decimales que se guardan: la clave más larga de PRECISION con la que
    empieza el nombre (`vsw_1` → `vsw`, `w_so_3` → `w_so`)."""
    candidatas = [k for k in PRECISION if variable == k or variable.startswith(k + "_")]
    return PRECISION[max(candidatas, key=len)] if candidatas else 2


def nombre_variable(campo):
    """Nombre de la variable en el NetCDF: el corto, más el nivel si hace falta."""
    if campo["nivel_tipo"] in ("isobaricInhPa", "depthBelowLandLayer", "depthBelowLand",
                               "soilLayer", "soil"):
        return f"{campo['nombre']}_{campo['nivel']}"
    return campo["nombre"]


def a_netcdf(campos, atributos: dict) -> bytes:
    """Junta campos de una misma pasada en un NetCDF (tiempo, lat, lon)."""
    import xarray as xr
    if not campos:
        raise ValueError("no hay campos que guardar")
    lats, lons = campos[0]["lats"], campos[0]["lons"]
    tiempos = sorted({c["validez"] for c in campos})
    it = {t: i for i, t in enumerate(tiempos)}
    pasada = campos[0]["pasada"]
    variables, unidades = {}, {}
    for c in campos:
        if c["valores"].shape != (len(lats), len(lons)):
            raise ValueError(f"{c['nombre']}: malla distinta al resto")
        v = nombre_variable(c)
        if v not in variables:
            variables[v] = np.full((len(tiempos), len(lats), len(lons)), np.nan, np.float32)
            unidades[v] = c["unidades"]
        hueco = variables[v][it[c["validez"]]]
        if not np.isnan(hueco).all():
            raise ValueError(f"{v} {c['validez']:%F %H}h aparece dos veces")
        variables[v][it[c["validez"]]] = c["valores"]
    t = np.array([np.datetime64(x.replace(tzinfo=None), "ns") for x in tiempos])
    ds = xr.Dataset(
        {v: (("time", "latitude", "longitude"), a, {"units": unidades[v]} if unidades[v] else {})
         for v, a in sorted(variables.items())},
        coords={"time": t, "latitude": lats.astype(np.float64),
                "longitude": lons.astype(np.float64),
                "step_hours": ("time", [(x - pasada).total_seconds() / 3600 for x in tiempos])})
    ds.attrs.update({k: str(v) for k, v in atributos.items()})
    ds.attrs["pasada"] = pasada.strftime("%Y-%m-%dT%H:%MZ")
    ds.attrs["dominio"] = f"N{NORTE} S{SUR} W{OESTE} E{ESTE}, malla nativa sin interpolar"
    cod = {}
    for v in ds.data_vars:
        cod[v] = {"zlib": True, "complevel": 6, "shuffle": True, "dtype": "float32",
                  "least_significant_digit": precision(v),
                  "chunksizes": (1, len(lats), len(lons))}
    cod["time"] = {"units": "hours since 1970-01-01", "dtype": "int64"}
    with tempfile.TemporaryDirectory() as d:
        ruta = Path(d) / "x.nc"
        ds.to_netcdf(ruta, engine="netcdf4", encoding=cod)
        return ruta.read_bytes()
