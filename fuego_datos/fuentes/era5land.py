"""ERA5-Land horario (Copernicus Climate Data Store), CC BY 4.0.

Reanálisis de 0,1° (~9 km), cada hora, con unos 5 días de retraso. Se pide un
día por petición, en NetCDF tal como lo entrega el CDS: es el formato que lee
la cadena de cálculo, así que el servidor no necesita librerías de GRIB. Como
hay variables instantáneas y acumuladas, el CDS lo entrega en un zip con dos
NetCDF, y se guarda así. ERA5-Land se puede volver a descargar cuando se
quiera, así que aquí lo importante es tenerlo al día, no la urgencia.

Credenciales: CDSAPI_URL y CDSAPI_KEY (o ~/.cdsapirc).
"""
import logging
import tempfile
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
RETRASO_MIN, VENTANA = 5, 20     # se intentan los días entre D-20 y D-5


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


def ejecutar(lote):
    hoy = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    hechas = 0
    for d in range(VENTANA, RETRASO_MIN - 1, -1):
        dia = hoy - timedelta(days=d)
        hecho = [o for o in lote.almacen.listar(lote.clave("horario", f"era5land_{dia:%Y%m%d}.", dia))]
        if hecho:
            continue
        log.info("ERA5-Land %s: pidiendo al CDS", f"{dia:%F}")
        try:
            datos = bajar_dia(dia)
        except Exception as e:
            # Los días más recientes aún no están: se para aquí y se reintenta mañana
            log.warning("ERA5-Land %s: no disponible todavía (%s)", f"{dia:%F}", str(e)[:200])
            break
        if len(datos) < 1_000_000:
            raise RuntimeError(f"ERA5-Land {dia:%F}: fichero sospechosamente pequeño ({len(datos)} bytes)")
        ext = "zip" if datos[:2] == b"PK" else "nc"
        clave = lote.clave("horario", f"era5land_{dia:%Y%m%d}.{ext}", dia)
        lote.guardar(clave, datos, origen=f"CDS reanalysis-era5-land {dia:%F}", licencia=LICENCIA)
        hechas += 1
    return hechas
