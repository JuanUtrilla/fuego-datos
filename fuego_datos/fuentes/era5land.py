"""ERA5-Land horario (Copernicus Climate Data Store), CC BY 4.0.

Reanálisis de 0,1° (~9 km), cada hora, con unos 5 días de retraso. Se pide un
día por petición, en NetCDF tal como lo entrega el CDS: es el formato que lee
la cadena de cálculo, así que el servidor no necesita librerías de GRIB. Como
hay variables instantáneas y acumuladas, el CDS lo entrega en un zip con dos
NetCDF, y se guarda así. ERA5-Land se puede volver a descargar cuando se
quiera, así que aquí lo importante es tenerlo al día, no la urgencia.

Desde mediados de octubre de 2026 el CDS publica los días a trozos: a D-5
puede traer de 2 a 17 horas, y tarda una o dos semanas en tener las 24. Se
guarda lo que haya, con las horas en los metadatos, y en cada pasada se
vuelven a pedir los días de la ventana que no estén completos; cuando llegan
más horas, el fichero se sustituye.

Credenciales: CDSAPI_URL y CDSAPI_KEY (o ~/.cdsapirc).
"""
import io
import logging
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import ESTE, NORTE, OESTE, SUR

log = logging.getLogger(__name__)

LICENCIA = ("CC BY 4.0. Contiene información modificada del Copernicus Climate Change "
            "Service. Ni la Comisión Europea ni ECMWF son responsables del uso que se haga")

VARIABLES = [
    "2m_temperature", "2m_dewpoint_temperature", "skin_temperature",
    "10m_u_component_of_wind", "10m_v_component_of_wind",
    "total_precipitation", "total_evaporation", "potential_evaporation",
    "surface_solar_radiation_downwards", "surface_thermal_radiation_downwards",
    "surface_pressure",
    "volumetric_soil_water_layer_1", "volumetric_soil_water_layer_2",
    "soil_temperature_level_1",
    "leaf_area_index_high_vegetation", "leaf_area_index_low_vegetation",
]
RETRASO_MIN, VENTANA = 5, 25     # se intentan los días entre D-25 y D-5
HORAS_DIA = 24


def bajar_dia(dia):
    import cdsapi
    cliente = cdsapi.Client(quiet=True, progress=False)
    peticion = {
        "variable": VARIABLES, "year": f"{dia:%Y}", "month": f"{dia:%m}", "day": [f"{dia:%d}"],
        "time": [f"{h:02d}:00" for h in range(24)],
        "area": [NORTE, OESTE, SUR, ESTE], "data_format": "netcdf", "download_format": "unarchived",
    }
    with tempfile.TemporaryDirectory() as d:
        ruta = Path(d) / "era5land"
        cliente.retrieve("reanalysis-era5-land", peticion, str(ruta))
        return ruta.read_bytes()


def horas(datos):
    """Horas que trae un fichero del CDS (NetCDF suelto o zip con dos)."""
    import netCDF4
    if datos[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            return min(horas(z.read(n)) for n in z.namelist() if n.endswith(".nc"))
    with netCDF4.Dataset("era5land", memory=datos) as ds:
        return len(ds.dimensions["valid_time"])


def horas_guardadas(almacen, clave):
    meta = almacen.metadatos(clave)
    if "horas" in meta:
        return int(meta["horas"])
    return horas(almacen.leer(clave))      # ficheros de antes de anotar las horas


def ejecutar(lote):
    hoy = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    hechas = 0
    for d in range(VENTANA, RETRASO_MIN - 1, -1):
        dia = hoy - timedelta(days=d)
        previos = list(lote.almacen.listar(lote.clave("horario", f"era5land_{dia:%Y%m%d}.", dia)))
        tenia = horas_guardadas(lote.almacen, previos[0].clave) if previos else 0
        if tenia >= HORAS_DIA:
            continue
        log.info("ERA5-Land %s: pidiendo al CDS (%d h guardadas)", f"{dia:%F}", tenia)
        try:
            datos = bajar_dia(dia)
        except Exception as e:
            # Los días más recientes aún no están: se para aquí y se reintenta en la siguiente pasada
            log.warning("ERA5-Land %s: no disponible todavía (%s)", f"{dia:%F}", str(e)[:200])
            break
        n = horas(datos)
        if n == 0:
            raise RuntimeError(f"ERA5-Land {dia:%F}: el CDS devolvió un fichero sin horas")
        if n <= tenia:
            log.info("ERA5-Land %s: sigue con %d de %d h", f"{dia:%F}", n, HORAS_DIA)
            continue
        ext = "zip" if datos[:2] == b"PK" else "nc"
        clave = lote.clave("horario", f"era5land_{dia:%Y%m%d}.{ext}", dia)
        if previos and previos[0].clave != clave:
            log.warning("ERA5-Land %s: cambia de formato (%s → %s); quedan los dos",
                        f"{dia:%F}", previos[0].clave, clave)
        if n < HORAS_DIA:
            log.warning("ERA5-Land %s: incompleto, %d de %d h", f"{dia:%F}", n, HORAS_DIA)
        lote.guardar(clave, datos, origen=f"CDS reanalysis-era5-land {dia:%F}",
                     licencia=LICENCIA, horas=n)
        hechas += 1
    return hechas
