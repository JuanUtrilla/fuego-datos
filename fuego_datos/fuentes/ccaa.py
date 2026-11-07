"""Índices oficiales de peligro de incendio de las comunidades autónomas.

Un job por comunidad (o por producto), para que el fallo de una no tape a las
demás. Ninguna de estas fuentes tiene archivo público de lo diario (salvo
Valencia y La Rioja, que dejan consultar fechas pasadas): lo que no se baja se
pierde. Se guarda el bruto tal cual (comprimido con gzip si es texto) y solo
cuando cambia. Detalle, horarios, tamaños y licencias en docs/indices_ccaa.md.

- cat-perill  Cataluña, mapa de perill d'incendi: rejilla de 5 km (ZIP con SHP) y
              tabla municipal (XLSX), hoy y mañana.
- cat-alfa    Cataluña, Pla Alfa por municipio y por comarca, hoy y mañana (ArcGIS).
- and-riesgo  Andalucía, índice de riesgo de REDIAM: GeoTIFF de los 3 días (WMS).
- nav-riesgo  Navarra, riesgo por municipio D0, D1 y D2 (WFS, GeoJSON).
- clm-ipp     Castilla-La Mancha, IPP por municipio (ArcGIS) y el PDF del parte.
- val-preemergencia  Comunitat Valenciana, nivel de preemergencia por zona (HTML).
- rio-ipogif  La Rioja, IPOGIF por zona de D+1 a D+7.

Si la respuesta no tiene la forma esperada, se guarda en `<producto>/.../fallo_*`
para ver qué ha cambiado y el job falla con el motivo.
"""
import hashlib
import io
import json
import logging
import re
import struct
import tarfile
import time
import unicodedata
import zipfile
from datetime import date, datetime, timedelta, timezone
from html import unescape
from urllib.parse import unquote, urljoin
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests

from ..almacen import comprimir
from ..red import SESION, TIEMPO, get

log = logging.getLogger(__name__)

HORA = ZoneInfo("Europe/Madrid")

LIC_GENCAT = ("Generalitat de Catalunya. Avís legal de gencat.cat: reutilització comercial i no comercial "
              "(Llei 37/2007, Llicència oberta d'ús d'informació - Catalunya), citant la font i la data "
              "d'actualització, sense alterar el contingut")
LIC_PERILL = "Departament d'Agricultura - Servei de Prevenció d'Incendis Forestals, mapa de perill d'incendi. " + LIC_GENCAT
LIC_ALFA = ("Generalitat de Catalunya - Cos d'Agents Rurals, Pla Alfa. Servicio de ArcGIS Online sin licencia "
            "propia; se aplica el avís legal de gencat.cat (reutilización comercial citando la fuente), sin confirmar")
LIC_REDIAM = ("Junta de Andalucía - REDIAM / Plan INFOCA, índice de riesgo de incendio forestal. Uso libre y "
              "gratuito citando autores (WMS); condiciones de uso del portal: reutilización también comercial "
              "citando fuente y fecha, sin alterar el contenido")
LIC_NAVARRA = ("Gobierno de Navarra - IDENA, riesgo de incendios. CC BY 4.0 según el geoportal de IDENA; el WFS "
               "declara «no conditions apply». Sin confirmar para esta capa")
LIC_INFOCAM = ("Junta de Comunidades de Castilla-La Mancha - C.O.R. INFOCAM, índice de propagación potencial. "
               "Aviso legal: reutilización conforme a la Ley 37/2007 citando la fuente; no excluye el uso comercial")
LIC_VAERSA = ("VAERSA / Generalitat Valenciana, nivel de preemergencia (boletín de AEMET). Aviso legal de "
              "prevencionincendiosgva.es: solo visualización y uso personal NO comercial. No usar en un "
              "producto comercial sin autorización")
LIC_RIOJA = ("Gobierno de La Rioja, IPOGIF. Aviso legal de larioja.org: reproducción autorizada citando la "
             "fuente; uso comercial sin confirmar")


class Formato(RuntimeError):
    """La fuente ha respondido, pero no con la forma esperada."""


def _hoy(lote):
    return lote.inicio.astimezone(HORA).date()


def _hhmmss(lote):
    return f"{lote.inicio:%H%M%S}"


def _sha(*trozos):
    h = hashlib.sha256()
    for t in trozos:
        h.update(t if isinstance(t, bytes) else str(t).encode())
        h.update(b"\0")
    return h.hexdigest()


def _cerca(fecha, hoy, antes=2, despues=4, que=""):
    if not hoy - timedelta(days=antes) <= fecha <= hoy + timedelta(days=despues):
        raise Formato(f"{que}: fecha {fecha} demasiado lejos de hoy ({hoy})")


def _fallo(lote, producto, nombre, datos, motivo):
    """Guarda la respuesta que no se ha entendido, para ver qué ha cambiado."""
    clave = lote.clave(producto, f"fallo_{nombre}_{_hhmmss(lote)}.gz")
    lote.guardar(clave, comprimir(datos), motivo=str(motivo)[:200])


def _cada(lote, que, elementos, funcion):
    """Ejecuta `funcion` con cada elemento aunque falle alguno y, al final,
    falla con todos los motivos. Devuelve cuántos ficheros nuevos se guardaron."""
    n, fallos = 0, []
    for e in elementos:
        try:
            n += funcion(e)
        except Exception as ex:
            log.error("%s %s: %s", que, e, ex)
            fallos.append(f"{e}: {ex}")
    if fallos:
        raise RuntimeError(f"{que}: {len(fallos)} fallo(s) ({n} guardados): " + "; ".join(fallos))
    return n


def _post(url, data, intentos=3):
    """POST con reintentos (red.SESION solo reintenta GET y HEAD)."""
    for intento in range(1, intentos + 1):
        try:
            r = SESION.post(url, data=data, timeout=TIEMPO)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if intento == intentos:
                raise
            log.warning("POST %s: %s; reintento en %d s", url, e, 10 * intento)
            time.sleep(10 * intento)


def _tar(ficheros, mtime):
    """[(nombre, bytes)] en un tar.gz con fecha fija (misma entrada, misma huella)."""
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w", format=tarfile.PAX_FORMAT) as t:
        for nombre, datos in ficheros:
            i = tarfile.TarInfo(nombre)
            i.size, i.mtime = len(datos), int(mtime.timestamp())
            t.addfile(i, io.BytesIO(datos))
    return comprimir(b.getvalue())


def _arcgis_fecha(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def _arcgis(url, que):
    """Metadatos de la capa (para `lastEditDate`) y todas sus filas sin geometría."""
    info = get(url, params={"f": "json"}).json()
    if "error" in info or "editingInfo" not in info:
        raise Formato(f"{que}: la capa no responde como se esperaba: {str(info)[:200]}")
    r = get(f"{url}/query", params={"where": "1=1", "outFields": "*", "returnGeometry": "false", "f": "json"})
    try:
        q = r.json()
    except ValueError:
        q = {"error": r.content[:200]}
    return _arcgis_fecha(info["editingInfo"]["lastEditDate"]), r.content, q


def _arcgis_filas(q, que, campos, minimo):
    if "error" in q or "features" not in q:
        raise Formato(f"{que}: respuesta sin filas: {str(q)[:200]}")
    if q.get("exceededTransferLimit"):
        raise Formato(f"{que}: la respuesta viene cortada (exceededTransferLimit)")
    filas = [f.get("attributes", {}) for f in q["features"]]
    if len(filas) < minimo:
        raise Formato(f"{que}: {len(filas)} filas; se esperaban al menos {minimo}")
    faltan = set(campos) - set(filas[0])
    if faltan:
        raise Formato(f"{que}: faltan los campos {sorted(faltan)} (hay {sorted(filas[0])})")
    return filas


# --- Cataluña: mapa de perill d'incendi -----------------------------------------------------

PERILL = "https://gencat.cat/medinatural/incendis/mapes/"
PERILL_FICHEROS = {           # el ZIP municipal (unos 15 MB cada uno) no se baja: es la tabla XLSX más la geometría
    "rejilla_avui": "mapa_perill_incendi.zip",
    "rejilla_dema": "mapa_perill_incendi_dema.zip",
    "municipis_avui": "taula_muni_perill_avui.xlsx",
    "municipis_dema": "taula_muni_perill_dema.xlsx",
}
PERILL_CELDAS = 1385          # rejilla de 5 km (07/11/2026)
PERILL_MUNICIPIS = 947
PERILL_NIVELES = {"baix", "moderat", "alt", "molt alt", "extrem"}


def dbf(datos: bytes):
    """Campos y filas de un DBF (dBase III) como listas de texto."""
    if len(datos) < 32:
        raise Formato("DBF demasiado corto")
    n, cabecera, largo = struct.unpack("<IHH", datos[4:12])
    campos, i = [], 32
    while i < cabecera - 1 and datos[i] != 0x0D:
        campos.append((datos[i:i + 11].split(b"\0")[0].decode("latin-1"), datos[i + 16]))
        i += 32
    filas = []
    for k in range(n):
        reg = datos[cabecera + k * largo: cabecera + (k + 1) * largo]
        if len(reg) < largo:
            raise Formato(f"DBF cortado en la fila {k} de {n}")
        if reg[:1] == b"*":          # borrada
            continue
        pos, fila = 1, {}
        for nombre, ancho in campos:
            fila[nombre] = reg[pos:pos + ancho].decode("latin-1").strip()
            pos += ancho
        filas.append(fila)
    return [c for c, _ in campos], filas


def perill_rejilla(datos: bytes):
    """ZIP del mapa de perill → (fecha válida, hora de creación «AAAAMMDD HH:MMh», nº de celdas, huella)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(datos))
    except zipfile.BadZipFile:
        raise Formato(f"no es un ZIP ({datos[:40]!r})") from None
    nombres = z.namelist()
    dbfs = [n for n in nombres if n.lower().endswith(".dbf")]
    if len(dbfs) != 1 or not any(n.lower().endswith(".shp") for n in nombres):
        raise Formato(f"el ZIP no trae un SHP con su DBF: {nombres}")
    campos, filas = dbf(z.read(dbfs[0]))
    faltan = {"perill", "Data", "Creacio"} - set(campos)
    if faltan:
        raise Formato(f"faltan los campos {sorted(faltan)} en el DBF (hay {campos})")
    if len(filas) != PERILL_CELDAS:
        raise Formato(f"{len(filas)} celdas; se esperaban {PERILL_CELDAS}")
    datas, creacions = {f["Data"] for f in filas}, {f["Creacio"] for f in filas}
    if len(datas) != 1 or len(creacions) != 1:
        raise Formato(f"varias fechas en el DBF: {sorted(datas)[:3]} {sorted(creacions)[:3]}")
    try:
        valido = datetime.strptime(datas.pop(), "%Y%m%d").date()
        niveles = {round(float(f["perill"])) for f in filas}
    except ValueError as e:
        raise Formato(f"valor raro en el DBF: {e}") from None
    if not niveles <= set(range(0, 6)):
        raise Formato(f"niveles de perill fuera de 0-5: {sorted(niveles)}")
    # la huella es el contenido de los ficheros, no el ZIP (que lleva fechas de compresión)
    huella = _sha(*[(n, z.read(n)) for n in sorted(nombres)])
    return valido, creacions.pop(), len(filas), huella


def xlsx_filas(datos: bytes):
    """Filas de la primera hoja de un XLSX como listas de texto, sin dependencias."""
    try:
        z = zipfile.ZipFile(io.BytesIO(datos))
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        textos = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                textos.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        hoja = ElementTree.fromstring(z.read("xl/worksheets/sheet1.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as e:
        raise Formato(f"XLSX ilegible: {e}") from None
    filas = []
    for row in hoja.iter(f"{{{ns['m']}}}row"):
        fila = []
        for c in row.findall("m:c", ns):
            v = c.find("m:v", ns)
            v = "" if v is None else v.text or ""
            fila.append(textos[int(v)] if c.get("t") == "s" and v else v)
        filas.append(fila)
    return filas, _sha(z.read("xl/worksheets/sheet1.xml"), z.read("xl/sharedStrings.xml")
                       if "xl/sharedStrings.xml" in z.namelist() else b"")


def perill_municipis(datos: bytes, horizonte):
    """XLSX municipal → (fecha válida, nº de municipios, huella). `horizonte`: AVUI o DEMA."""
    filas, huella = xlsx_filas(datos)
    cab = next((i for i, f in enumerate(filas) if "CODI" in f), None)
    if cab is None:
        raise Formato("no encuentro la cabecera CODI en el XLSX")
    nombres = filas[cab]
    esperadas = ["CODI", "MUNICIPI", "COMARCA", f"PERILL_{horizonte}", f"DATA_{horizonte}"]
    if [c for c in nombres if c] != esperadas:
        raise Formato(f"columnas {nombres}; se esperaban {esperadas}")
    i0 = nombres.index("CODI")
    datos_m = [f[i0:i0 + 5] for f in filas[cab + 1:] if len(f) > i0 + 4 and re.fullmatch(r"\d{5}", f[i0])]
    if len(datos_m) != PERILL_MUNICIPIS:
        raise Formato(f"{len(datos_m)} municipios; se esperaban {PERILL_MUNICIPIS}")
    niveles = {f[3].strip().lower() for f in datos_m}
    if not niveles <= PERILL_NIVELES:
        raise Formato(f"niveles desconocidos: {sorted(niveles - PERILL_NIVELES)}")
    fechas = {f[4] for f in datos_m}
    if len(fechas) != 1:
        raise Formato(f"varias fechas en el XLSX: {sorted(fechas)[:3]}")
    try:
        valido = datetime.strptime(fechas.pop(), "%d/%m/%y").date()
    except ValueError as e:
        raise Formato(f"fecha rara en el XLSX: {e}") from None
    return valido, len(datos_m), huella


def cat_perill(lote):
    hoy = _hoy(lote)

    def uno(nombre):
        fichero = PERILL_FICHEROS[nombre]
        url = PERILL + fichero
        r = get(url)
        try:
            if fichero.endswith(".zip"):
                valido, creacio, n, huella = perill_rejilla(r.content)
                sello = re.sub(r"\D", "", creacio)[:12]
                extra = {"creacio": creacio}
            else:
                valido, n, huella = perill_municipis(r.content, nombre.rsplit("_", 1)[1].upper())
                sello, extra = _hhmmss(lote), {}
            _cerca(valido, hoy, 2, 2, f"perill {nombre}")
        except Formato as e:
            _fallo(lote, "perill", nombre, r.content, e)
            raise
        ext = fichero.rsplit(".", 1)[1]
        clave = lote.clave("perill", f"perill_{nombre}_{valido:%Y%m%d}_{sello}.{ext}")
        if lote.almacen.existe(clave):
            clave = lote.clave("perill", f"perill_{nombre}_{valido:%Y%m%d}_{sello}_{_hhmmss(lote)}.{ext}")
        log.info("perill %s: %s, %d elementos", nombre, valido, n)
        return int(lote.guardar_si_cambia(f"gencat_perill_{nombre}", clave, r.content, origen=url,
                                          huella=huella, licencia=LIC_PERILL, valido=f"{valido}",
                                          last_modified=r.headers.get("Last-Modified") or "", **extra))

    return _cada(lote, "perill", PERILL_FICHEROS, uno)


# --- Cataluña: Pla Alfa ------------------------------------------------------------------------

ALFA = "https://services7.arcgis.com/ZCqVt1fRXwwK6GF4/arcgis/rest/services/"
ALFA_CAPAS = {   # la vista municipal «_FL_2_view» se quedó congelada en diciembre de 2025: se usa la «alternatiu»
    "municipal_avui": ("Pla_Alfa_Municipal_Avui_FL_alternatiu_VW/FeatureServer/0", "PERIL_M", 900),
    "municipal_dema": ("pla_alfa_municipal_dema_FL_VW/FeatureServer/5", "PERIL_M", 900),
    "comarcal_avui": ("Pla_Alfa_Comarcal_Avui_FL_VW/FeatureServer/1", "PERILL", 40),
    "comarcal_dema": ("Pla_Alfa_Comarcal_Dema_FL_VW/FeatureServer/4", "PERILL", 40),
}


def cat_alfa(lote):
    def uno(nombre):
        ruta, campo, minimo = ALFA_CAPAS[nombre]
        url = ALFA + ruta
        editado, bruto, q = _arcgis(url, f"Pla Alfa {nombre}")
        try:
            filas = _arcgis_filas(q, f"Pla Alfa {nombre}", [campo, "CODICOMAR"], minimo)
            niveles = {f[campo] for f in filas}
            if not all(isinstance(v, int) and 0 <= v <= 5 for v in niveles):
                raise Formato(f"Pla Alfa {nombre}: niveles raros {sorted(map(str, niveles))}")
        except Formato as e:
            _fallo(lote, "pla_alfa", nombre, bruto, e)
            raise
        log.info("Pla Alfa %s: %d filas, editado %s, niveles %s", nombre, len(filas), editado, sorted(niveles))
        clave = lote.clave("pla_alfa", f"alfa_{nombre}_{editado:%Y%m%d%H%M}_{_hhmmss(lote)}.json.gz")
        return int(lote.guardar_si_cambia(f"gencat_alfa_{nombre}", clave, comprimir(bruto),
                                          origen=f"{url}/query", licencia=LIC_ALFA,
                                          editado=editado.isoformat(timespec="seconds")))

    return _cada(lote, "Pla Alfa", ALFA_CAPAS, uno)


# --- Andalucía: REDIAM ----------------------------------------------------------------------------

REDIAM = "https://www.juntadeandalucia.es/medioambiente/mapwms/REDIAM_Indice_Riesgo_Incendios_Diario"
REDIAM_DIAS = 3
REDIAM_PIXEL = 240            # m; el TIF original de la Junta es de unos 240 m en EPSG:25830
TITULO = re.compile(r"Índice riesgo incendios (\d{2}-\d{2}-\d{4}) \(predicción (\d{2}-\d{2}-\d{4})\)")


def rediam_capas(caps: bytes):
    """GetCapabilities → {k: (fecha válida, fecha de la predicción, (minx, miny, maxx, maxy) en EPSG:25830)}."""
    texto = caps.decode("utf-8", "replace")
    capas = {}
    for k in range(1, REDIAM_DIAS + 1):
        m = re.search(rf"<Name>indice_riesgo_dia_{k}</Name>\s*<Title>([^<]*)</Title>(.*?)</Layer>", texto, re.S)
        if not m:
            raise Formato(f"REDIAM: no está la capa indice_riesgo_dia_{k} en GetCapabilities")
        t = TITULO.search(m.group(1))
        caja = re.search(r'<BoundingBox CRS="EPSG:25830"\s+minx="([^"]+)" miny="([^"]+)" maxx="([^"]+)" maxy="([^"]+)"',
                         m.group(2))
        if not t or not caja:
            raise Formato(f"REDIAM dia_{k}: título o caja inesperados: {m.group(1)!r}")
        valido, pred = (datetime.strptime(x, "%d-%m-%Y").date() for x in t.groups())
        capas[k] = (valido, pred, tuple(float(x) for x in caja.groups()))
    preds = {c[1] for c in capas.values()}
    if len(preds) != 1:
        raise Formato(f"REDIAM: las capas son de predicciones distintas: {sorted(preds)}")
    return capas


def and_riesgo(lote):
    """Si los títulos de GetCapabilities (que fechan cada capa) son los de lo último
    guardado, no se baja nada más. Fuera de campaña el servicio se queda parado
    (el 07/11/2026 seguía en la predicción del 21/10)."""
    caps = get(REDIAM, params={"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"}).content
    try:
        capas = rediam_capas(caps)
    except Formato as e:
        _fallo(lote, "riesgo", "capabilities", caps, e)
        raise
    pred = capas[1][1]
    huella = _sha(*[(k, v[0], v[1]) for k, v in sorted(capas.items())])
    if lote.ultimo("rediam_riesgo").get("sha256") == huella:
        log.info("REDIAM: la predicción del %s ya está guardada", pred)
        return 0

    def uno(k):
        valido, _, (x0, y0, x1, y1) = capas[k]
        clave = lote.clave("riesgo", f"riesgo_pred{pred:%Y%m%d}_d{k}_{valido:%Y%m%d}.tif.gz", pred)
        if lote.almacen.existe(clave):
            return 0
        ancho, alto = round((x1 - x0) / REDIAM_PIXEL), round((y1 - y0) / REDIAM_PIXEL)
        params = {"service": "WMS", "version": "1.3.0", "request": "GetMap", "layers": f"indice_riesgo_dia_{k}",
                  "styles": "", "crs": "EPSG:25830", "bbox": f"{x0},{y0},{x1},{y1}", "width": ancho,
                  "height": alto, "format": "image/tiff"}
        r = get(REDIAM, params=params)
        if r.content[:4] not in (b"II*\x00", b"MM\x00*"):
            _fallo(lote, "riesgo", f"d{k}", r.content, "no es un TIFF")
            raise Formato(f"REDIAM dia_{k}: no es un TIFF ({r.headers.get('content-type')}: {r.content[:80]!r})")
        log.info("REDIAM dia_%d: %s (predicción %s), %dx%d", k, valido, pred, ancho, alto)
        lote.guardar(clave, comprimir(r.content), origen=f"{REDIAM} GetMap indice_riesgo_dia_{k}",
                     licencia=LIC_REDIAM, valido=f"{valido}", prediccion=f"{pred}",
                     peticion=json.dumps(params), titulo=f"Índice riesgo incendios {valido:%d-%m-%Y} "
                     f"(predicción {pred:%d-%m-%Y})")
        return 1

    n = _cada(lote, "REDIAM", range(1, REDIAM_DIAS + 1), uno)
    # al final, y solo si han ido bien los tres días: GetCapabilities con los títulos,
    # que marca la predicción como guardada
    lote.guardar_si_cambia("rediam_riesgo",
                           lote.clave("riesgo", f"capabilities_pred{pred:%Y%m%d}_{_hhmmss(lote)}.xml.gz", pred),
                           comprimir(caps), origen=REDIAM, huella=huella, licencia=LIC_REDIAM,
                           prediccion=f"{pred}")
    return n


# --- Navarra --------------------------------------------------------------------------------------

NAVARRA = "https://inspire.navarra.es/services/riesgoIncendios/wfs"
NAVARRA_DIAS = 3              # vw_riesgomunicipiod0 (hoy) a d2
NAVARRA_MIN = 280             # 289 municipios y facerías (07/11/2026)
NAVARRA_CAMPOS = {"Municipio", "Alerta", "Fecha riesgo", "IdRiesgo", "Zona Meteoalerta"}


def navarra_geojson(datos: bytes, que):
    """GeoJSON del WFS → (fecha del riesgo, nº de recintos, huella sin los id ni el timeStamp,
    que cambian en cada petición)."""
    try:
        geo = json.loads(datos)
    except ValueError:
        raise Formato(f"{que}: no es JSON ({datos[:150]!r})") from None
    feats = geo.get("features") or []
    if len(feats) < NAVARRA_MIN:
        raise Formato(f"{que}: {len(feats)} recintos; se esperaban al menos {NAVARRA_MIN}")
    props = [f.get("properties") or {} for f in feats]
    faltan = NAVARRA_CAMPOS - set(props[0])
    if faltan:
        raise Formato(f"{que}: faltan los campos {sorted(faltan)} (hay {sorted(props[0])})")
    fechas = {p["Fecha riesgo"] for p in props}
    if len(fechas) != 1:
        raise Formato(f"{que}: varias fechas {sorted(fechas)[:3]}")
    niveles = {p["IdRiesgo"] for p in props}
    if not all(isinstance(v, int) and 0 <= v <= 5 for v in niveles):
        raise Formato(f"{que}: niveles fuera de 0-5: {sorted(map(str, niveles))}")
    try:
        fecha = datetime.strptime(fechas.pop(), "%d/%m/%Y").date()
    except ValueError as e:
        raise Formato(f"{que}: fecha rara: {e}") from None
    huella = _sha(json.dumps([(f.get("properties"), f.get("geometry")) for f in feats],
                             sort_keys=True, ensure_ascii=False))
    return fecha, len(feats), huella


def nav_riesgo(lote):
    hoy = _hoy(lote)

    def uno(d):
        capa = f"riesgoIncendios:vw_riesgomunicipiod{d}"
        r = get(NAVARRA, params={"service": "WFS", "version": "2.0.0", "request": "GetFeature",
                                 "typeNames": capa, "outputFormat": "application/json"})
        try:
            fecha, n, huella = navarra_geojson(r.content, f"Navarra d{d}")
            _cerca(fecha, hoy, 1, 3, f"Navarra d{d}")
        except Formato as e:
            _fallo(lote, "riesgo", f"d{d}", r.content, e)
            raise
        log.info("Navarra d%d: %s, %d recintos", d, fecha, n)
        clave = lote.clave("riesgo", f"riesgo_d{d}_{fecha:%Y%m%d}_{_hhmmss(lote)}.geojson.gz")
        return int(lote.guardar_si_cambia(f"navarra_riesgo_d{d}", clave, comprimir(r.content),
                                          origen=f"{NAVARRA} {capa}", huella=huella,
                                          licencia=LIC_NAVARRA, valido=f"{fecha}"))

    return _cada(lote, "Navarra", range(NAVARRA_DIAS), uno)


# --- Castilla-La Mancha ---------------------------------------------------------------------------

INFOCAM = ("https://services-eu1.arcgis.com/LVA9E9zjh6QfM7Mo/arcgis/rest/services/"
           "IPP_Administrativo_Web_Vista_2025/FeatureServer/0")
INFOCAM_PAGINA = "https://infocam.castillalamancha.es/indice-de-propagacion-potencial"
INFOCAM_MIN = 900             # 921 municipios (07/11/2026)
INFOCAM_NIVELES = {"bajo", "moderado", "alto", "muy alto", "extremo"}
PERIODO = re.compile(r"(\d{2}/\d{2}/\d{4})(?:\s*-\s*(\d{2}/\d{2}/\d{4}))?")


def infocam_ipp(q):
    """Respuesta de la consulta → (primer día, último día, nº de municipios)."""
    filas = _arcgis_filas(q, "IPP", ["NATCODE", "IPPadmin", "Fecha_Text"], INFOCAM_MIN)
    niveles = {(f["IPPadmin"] or "").strip().lower() for f in filas}
    if not niveles <= INFOCAM_NIVELES:
        raise Formato(f"IPP: niveles desconocidos {sorted(niveles - INFOCAM_NIVELES)}")
    periodos = {f["Fecha_Text"] for f in filas}
    m = PERIODO.fullmatch((periodos.pop() if len(periodos) == 1 else "").strip())
    if not m:
        raise Formato(f"IPP: periodo raro o varios: {sorted(map(str, periodos))[:3]}")
    d0 = datetime.strptime(m.group(1), "%d/%m/%Y").date()
    d1 = datetime.strptime(m.group(2), "%d/%m/%Y").date() if m.group(2) else d0
    return d0, d1, len(filas)


def infocam_pdf(pagina: str):
    """El primer PDF de la lista de partes es el último publicado."""
    m = re.search(r'href=\s*"?(/sites/default/files/[^\s">]+\.pdf)', pagina)
    if not m:
        raise Formato("IPP: no encuentro el enlace al PDF en la página")
    return urljoin(INFOCAM_PAGINA, m.group(1))


def _nombre_limpio(url):
    nombre = unquote(url.rsplit("/", 1)[1])
    nombre = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9._-]+", "_", nombre).strip("_")


def clm_ipp(lote):
    editado, bruto, q = _arcgis(INFOCAM, "IPP")
    try:
        d0, d1, n = infocam_ipp(q)
    except Formato as e:
        _fallo(lote, "ipp", "consulta", bruto, e)
        raise
    log.info("IPP: %s a %s, %d municipios, editado %s", d0, d1, n, editado)
    clave = lote.clave("ipp", f"ipp_{d0:%Y%m%d}_{d1:%Y%m%d}_{_hhmmss(lote)}.json.gz")
    nuevos = int(lote.guardar_si_cambia("infocam_ipp", clave, comprimir(bruto), origen=f"{INFOCAM}/query",
                                        licencia=LIC_INFOCAM, valido=f"{d0}/{d1}",
                                        editado=editado.isoformat(timespec="seconds")))
    # el PDF del parte (unos 0,5 MB): la tabla por municipio, igual que la capa
    pagina = get(INFOCAM_PAGINA).content
    try:
        url = infocam_pdf(pagina.decode("utf-8", "replace"))
    except Formato as e:
        _fallo(lote, "ipp", "pagina", pagina, e)
        raise
    pdf = get(url).content
    if not pdf.startswith(b"%PDF"):
        raise Formato(f"IPP: {url} no es un PDF ({pdf[:60]!r})")
    clave = lote.clave("ipp", f"ipp_pdf_{_hhmmss(lote)}_{_nombre_limpio(url)}")
    nuevos += int(lote.guardar_si_cambia("infocam_ipp_pdf", clave, pdf, origen=url, licencia=LIC_INFOCAM))
    return nuevos


# --- Comunitat Valenciana -------------------------------------------------------------------------

VAERSA = "https://prevencionincendiosgva.es/Meteorologia/NivelPreemergenciaList"
VAERSA_ZONAS = ["Z. 1N", "Z. 1S", "Z. 2", "Z. 3", "Z. 4", "Z. 5", "Z. 6"]


def _celdas(tr):
    return [re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", c))).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]


def vaersa_tabla(pagina: str):
    """HTML de la lista → (trozo de la tabla, {fecha: {"Alerta": [7 niveles], "Tormenta": [...]}})."""
    m = re.search(r'<table id="table-riesgo-incendio".*?</table>', pagina, re.S)
    if not m:
        raise Formato("Valencia: no está la tabla table-riesgo-incendio")
    tabla = m.group(0)
    filas = re.findall(r"<tr[^>]*>(.*?)</tr>", tabla, re.S)
    cab = _celdas(filas[0]) if filas else []
    if cab[1:8] != VAERSA_ZONAS:
        raise Formato(f"Valencia: cabecera {cab}; se esperaban las zonas {VAERSA_ZONAS}")
    dias, fecha = {}, None
    for tr in filas[1:]:
        c = _celdas(tr)
        if c and re.fullmatch(r"\d{2}/\d{2}/\d{4}", c[0]):
            fecha = datetime.strptime(c[0], "%d/%m/%Y").date()
            c = c[1:]
        if fecha is None or len(c) < 8 or c[0] not in ("Alerta", "Tormenta"):
            raise Formato(f"Valencia: fila inesperada {c[:9]}")
        niveles = c[1:8]
        if not all(v in ("", "1", "2", "3") for v in niveles):
            raise Formato(f"Valencia {fecha} {c[0]}: niveles raros {niveles}")
        dias.setdefault(fecha, {})[c[0]] = niveles
    if not dias:
        raise Formato("Valencia: la tabla no tiene ningún día")
    return tabla, dias


def val_preemergencia(lote):
    """Ayer, hoy y los dos días siguientes (si ya están). La tabla trae el nivel de
    «Alerta» (1 baja-media, 2 alta, 3 extrema) y el de «Tormenta» por zona."""
    hoy = _hoy(lote)
    params = {"page": 1, "desde": f"{hoy - timedelta(days=1):%d/%m/%Y}", "hasta": f"{hoy + timedelta(days=2):%d/%m/%Y}",
              "riesgoID": 0, "provinciaID": 0, "zonaID": 0}
    r = get(VAERSA, params=params)
    try:
        tabla, dias = vaersa_tabla(r.content.decode("utf-8", "replace"))
        for d in dias:
            _cerca(d, hoy, 1, 2, "Valencia")
    except Formato as e:
        _fallo(lote, "preemergencia", "lista", r.content, e)
        raise
    log.info("Valencia: %s", {f"{d}": v.get("Alerta") for d, v in sorted(dias.items())})
    if hoy not in dias:
        log.warning("Valencia: aún no está el nivel de hoy (%s)", hoy)
    clave = lote.clave("preemergencia", f"preemergencia_{max(dias):%Y%m%d}_{_hhmmss(lote)}.html.gz")
    return int(lote.guardar_si_cambia("vaersa_preemergencia", clave, comprimir(r.content), origen=r.url,
                                      huella=_sha(tabla), licencia=LIC_VAERSA,
                                      valido=f"{min(dias)}/{max(dias)}"))


# --- La Rioja -------------------------------------------------------------------------------------

RIOJA = "https://bi.larioja.org/pentaho/riesgo_incendios/"
RIOJA_DATOS = RIOJA + "datos.jsp"
RIOJA_DIAS = 7
RIOJA_ZONAS = 5
CADENA = re.compile(r"Fecha: </strong>(\d{1,2}/\d{1,2}/\d{4})<br>.*?Riesgo:</span>\s*([^(<]+?)\s*\(([\d.,]+)\)")


def rioja_referencia(pagina: str):
    """Última fecha de referencia que ofrece la página (el valor del selector)."""
    m = re.search(r'id="startDate1"[^>]*value="(\d{2}/\d{2}/\d{4})"', pagina)
    if not m:
        raise Formato("La Rioja: no encuentro la fecha de referencia (startDate1) en la página")
    return datetime.strptime(m.group(1), "%d/%m/%Y").date()


def _fecha_rioja(d: date):
    return f"{d.day}/{d.month}/{d.year}"          # como toLocaleDateString("es") del navegador


def rioja_datos(js: str, fecha: date):
    """Respuesta de datos.jsp → [(zona, nivel, valor)]; [] si no hay datos para esa fecha."""
    bloques = {n: re.search(rf"var {n} = \[(.*?)\]", js, re.S) for n in ("aCodigos", "aCadena", "aColor")}
    if not all(bloques.values()):
        raise Formato(f"La Rioja: respuesta sin aCodigos/aCadena/aColor ({js[:120]!r})")
    zonas = re.findall(r'"([^"]*)"', bloques["aCodigos"].group(1))
    cadenas = re.findall(r'"((?:[^"\\]|\\.)*)"', bloques["aCadena"].group(1))
    if not zonas and not cadenas:
        return []
    if len(zonas) != RIOJA_ZONAS or len(cadenas) != len(zonas):
        raise Formato(f"La Rioja {fecha}: {len(zonas)} zonas y {len(cadenas)} textos; se esperaban {RIOJA_ZONAS}")
    out = []
    for z, c in zip(zonas, cadenas):
        m = CADENA.search(c)
        if not m:
            raise Formato(f"La Rioja {fecha} {z}: texto inesperado {c[:150]!r}")
        if m.group(1) != _fecha_rioja(fecha):
            raise Formato(f"La Rioja {z}: fecha {m.group(1)}; se pedía {_fecha_rioja(fecha)}")
        out.append((z, m.group(2), float(m.group(3).replace(",", "."))))
    return out


def rio_ipogif(lote):
    """Un tar.gz por fecha de referencia con las 7 respuestas de datos.jsp (D+1 a D+7)
    tal cual. Se pide la última fecha que ofrece la página y la anterior (por si
    una pasada falló; el servidor guarda histórico desde 2023), si son posteriores
    a la última guardada."""
    pagina = get(RIOJA).content
    try:
        ultima = rioja_referencia(pagina.decode("utf-8", "replace"))
        _cerca(ultima, _hoy(lote), 3, 1, "La Rioja")
    except Formato as e:
        _fallo(lote, "ipogif", "pagina", pagina, e)
        raise

    m = re.search(r"ipogif_ref(\d{8})", lote.ultimo("larioja_ipogif").get("clave", ""))
    guardada = datetime.strptime(m.group(1), "%Y%m%d").date() if m else date.min
    pendientes = [r for r in (ultima - timedelta(days=1), ultima) if r > guardada]
    if not pendientes:
        log.info("La Rioja: la referencia %s ya está guardada", ultima)

    def uno(ref):
        clave = lote.clave("ipogif", f"ipogif_ref{ref:%Y%m%d}.tar.gz", ref)
        ficheros, peticiones, resumen = [], [], []
        for k in range(1, RIOJA_DIAS + 1):
            fecha = ref + timedelta(days=k)
            datos = {"fecha_referencia": f"{ref:%d/%m/%Y}", "fecha_datos": _fecha_rioja(fecha)}
            r = _post(RIOJA_DATOS, datos)
            nombre = f"d{k}_{fecha:%Y%m%d}.js"
            ficheros.append((nombre, r.content))
            peticiones.append({"url": RIOJA_DATOS, "post": datos, "http": r.status_code, "fichero": nombre})
            try:
                zonas = rioja_datos(r.content.decode("utf-8", "replace"), fecha)
            except Formato as e:
                _fallo(lote, "ipogif", f"ref{ref:%Y%m%d}_d{k}", r.content, e)
                raise
            if not zonas:
                if ref == ultima:
                    raise Formato(f"La Rioja: la referencia {ref} no trae datos para {fecha}")
                log.info("La Rioja: la referencia %s no tiene datos", ref)
                return 0
            resumen.append([fecha.isoformat(), [list(z) for z in zonas]])
            time.sleep(0.5)
        ficheros.append(("peticiones.json", json.dumps(peticiones, indent=1, ensure_ascii=False).encode()))
        log.info("La Rioja ref %s: %s", ref, resumen[0])
        return int(lote.guardar_si_cambia("larioja_ipogif", clave,
                                          _tar(ficheros, datetime(ref.year, ref.month, ref.day, tzinfo=timezone.utc)),
                                          origen=RIOJA_DATOS, licencia=LIC_RIOJA,
                                          valido=f"{ref + timedelta(days=1)}/{ref + timedelta(days=RIOJA_DIAS)}",
                                          referencia=f"{ref}"))

    return _cada(lote, "La Rioja", pendientes, uno)


# --- vigilancia: límites según la campaña ---------------------------------------------------------

def en_campana(hoy=None, desde=(6, 1), hasta=(10, 15)):
    hoy = hoy or datetime.now(HORA).date()
    return desde <= (hoy.month, hoy.day) <= hasta


def limite_and(hoy=None):
    """REDIAM solo publica en campaña: fuera, `None` (solo se exige que el job termine bien)."""
    return 36 if en_campana(hoy) else None


def limite_clm(hoy=None):
    """INFOCAM: un parte diario en campaña; fuera, uno por semana (de martes a lunes).
    192 h caben en los 9 días que el bucket guarda los manifiestos."""
    return 36 if en_campana(hoy) else 192
