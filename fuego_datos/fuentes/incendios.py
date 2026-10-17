"""Lo que ha ardido: focos FIRMS, perímetros EFFIS y partes del MITECO.

- FIRMS (NASA): focos activos de VIIRS (tres satélites) y MODIS en el dominio,
  últimas 48 h. NASA no restringe el uso; pide citar FIRMS.
- EFFIS (Copernicus EMS): perímetros de áreas quemadas de la temporada en
  curso, España y Portugal. CC BY 4.0. Se guarda una foto diaria, porque los
  perímetros se corrigen y amplían durante días.
- MITECO: los dos partes de intervenciones con medios del Estado, que se
  sobrescriben cada día en la misma dirección: el definitivo del día anterior
  y el provisional del día en curso. Ley 37/2007, citando la fuente.
"""
import json
import logging

from ..almacen import comprimir
from ..config import ESTE, NORTE, OESTE, SUR, var
from ..red import get

log = logging.getLogger(__name__)

FIRMS = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
FIRMS_FUENTES = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT")
FIRMS_LIC = ("We acknowledge the use of data from NASA's Fire Information for Resource "
             "Management System (FIRMS), part of NASA's ESDIS")

EFFIS_WFS = "https://maps.effis.emergency.copernicus.eu/effis"
EFFIS_CAPA = "ms:modis.ba.poly.season"
EFFIS_LIC = "CC BY 4.0. © Unión Europea, Copernicus EMS - EFFIS. Filtrado a ES y PT"

MITECO = "https://www.miteco.gob.es/content/dam/miteco/es/biodiversidad/temas/incendios-forestales"
MITECO_PARTES = {"definitivo": f"{MITECO}/ActuacionesMITECO-definitivo.pdf",
                 "provisional": f"{MITECO}/ActuacionesMITECO-provisional.pdf"}
MITECO_LIC = "Fuente: Ministerio para la Transición Ecológica y el Reto Demográfico (Ley 37/2007)"


def firms(lote, dias=2):
    clave_api = var("FIRMS_MAP_KEY", obligatoria=True)
    n = 0
    for fuente in FIRMS_FUENTES:
        url = f"{FIRMS}/{clave_api}/{fuente}/{OESTE},{SUR},{ESTE},{NORTE}/{dias}"
        texto = get(url).text
        if not texto.startswith("latitude"):
            raise RuntimeError(f"FIRMS {fuente}: respuesta inesperada: {texto[:200]!r}")
        filas = texto.count("\n") - 1
        clave = lote.clave(f"firms_{fuente.lower()}", f"{fuente.lower()}_{lote.inicio:%H%M%S}.csv.gz")
        origen = url.replace(clave_api, "<MAP_KEY>")
        if lote.guardar_si_cambia(f"firms_{fuente.lower()}", clave, comprimir(texto.encode()),
                                  origen=origen, licencia=FIRMS_LIC):
            log.info("FIRMS %s: %d focos", fuente, filas)
            n += 1
    return n


def effis(lote):
    r = get(EFFIS_WFS, params={
        "service": "WFS", "version": "1.1.0", "request": "GetFeature", "typename": EFFIS_CAPA,
        "outputformat": "application/json; subtype=geojson", "srsname": "EPSG:4326",
        "bbox": f"{SUR},{OESTE},{NORTE},{ESTE},EPSG:4326"})
    geo = json.loads(r.content.decode("utf-8"))
    total = len(geo.get("features", []))
    geo["features"] = [f for f in geo.get("features", [])
                       if (f.get("properties") or {}).get("COUNTRY") in ("ES", "PT")]
    if total and not geo["features"]:
        raise RuntimeError("EFFIS: hay perímetros pero ninguno de ES o PT; ¿ha cambiado el formato?")
    log.info("EFFIS: %d perímetros de ES y PT (de %d en el dominio)", len(geo["features"]), total)
    datos = comprimir(json.dumps(geo, ensure_ascii=False, separators=(",", ":")).encode())
    clave = lote.clave("ba_temporada", f"effis_ba_temporada_{lote.inicio:%Y%m%d}.geojson.gz")
    return int(lote.guardar_si_cambia("effis_ba_temporada", clave, datos,
                                      origen=f"{EFFIS_WFS} {EFFIS_CAPA}", licencia=EFFIS_LIC))


def miteco(lote):
    n = 0
    for parte, url in MITECO_PARTES.items():
        datos = get(url).content
        if not datos.startswith(b"%PDF"):
            raise RuntimeError(f"MITECO {parte}: no es un PDF ({datos[:60]!r})")
        clave = lote.clave(parte, f"parte_{parte}_{lote.inicio:%H%M%S}.pdf")
        n += lote.guardar_si_cambia(f"miteco_{parte}", clave, datos, origen=url, licencia=MITECO_LIC)
    return n
