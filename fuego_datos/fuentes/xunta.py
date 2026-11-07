"""Xunta de Galicia: índice de risco diario de incendio forestal (IRDI).

FWI canadiense calculado con el WRF de MeteoGalicia a 200 m; por concello se
publica solo el nivel del percentil 50 (baixo, moderado, alto, moi alto,
extremo), sin el valor del FWI, para los 313 concellos y de hoy a +3 días. Se
actualiza una vez al día, hacia las 08:48 UTC, y el servidor solo guarda esos
4 días: lo que no se baja se pierde.

Cada pasada:
1. la página (GET): «Actualizado o …» y la fecha de cada pestaña (dia_1 = hoy);
2. los cuatro mapas JPG (irdi_d0 a d3, sin georreferenciar), si han cambiado;
3. si esa actualización aún no está guardada, la tabla de cada día por el
   `views/ajax` de Drupal (POST, 50 filas por página, 7 páginas por día) y la
   página otra vez, para comprobar que no se ha actualizado mientras tanto.
Se guarda el bruto (página, los JSON y un registro de las peticiones) en un
tar.gz y el parseo en un CSV con el código INE.

El servidor no manda el certificado intermedio de GlobalSign, así que se
verifica con las CA de certifi más ese intermedio (en `recursos/`).

Reutilización comercial permitida citando la fuente. Detalle en docs/xunta_irdi.md.
"""
import csv
import functools
import html
import io
import json
import logging
import re
import tarfile
import tempfile
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import certifi
import requests

from ..almacen import comprimir

log = logging.getLogger(__name__)

PAXINA = "https://mediorural.xunta.gal/gl/temas/defensa-monte/irdi"
AJAX = "https://mediorural.xunta.gal/gl/views/ajax"
AJAX_CUERPO = {"view_name": "tabla_irdi", "view_display_id": "block_1",
               "view_path": "/node/1161", "pager_element": "0"}    # + view_args=dia_N; page va en la URL
MAPA = "https://mediorural.xunta.gal/sites/default/files/mapa-irdi/irdi_d{}.jpg"
LICENCIA = "Xunta de Galicia – Consellería do Medio Rural, IRDI. Reutilización permitida citando a fonte"
USER_AGENT = "fuego-datos/1.0 (+https://github.com/JuanUtrilla/fuego-datos)"
RECURSOS = Path(__file__).resolve().parent.parent / "recursos"
INTERMEDIO = RECURSOS / "globalsign_rsa_ov_ssl_ca_2018.pem"
HORA = ZoneInfo("Europe/Madrid")

CONCELLOS = 313
DIAS = 4                  # pestañas dia_1 (hoy) a dia_4
MAPAS = 4                 # irdi_d0 (hoy) a irdi_d3
MAX_PAXINAS = 12          # 313 filas de 50 en 50 son 7; margen por si cambia el tamaño
PAUSA = 1.5               # s entre peticiones
INTENTOS = 3
TIEMPO = (20, 60)
NIVEIS = {"baixo": 1, "moderado": 2, "alto": 3, "moi alto": 4, "extremo": 5}
MESES = {m: i for i, m in enumerate(
    ("xaneiro", "febreiro", "marzo", "abril", "maio", "xuño", "xullo", "agosto",
     "setembro", "outubro", "novembro", "decembro"), 1)}
COLUMNAS = ("fecha_validez", "dia", "actualizado", "codigo_ine", "concello", "nivel", "nivel_nome")


@functools.cache
def _certificados():
    """Fichero con las CA de certifi más el intermedio de GlobalSign."""
    f = tempfile.NamedTemporaryFile("w", prefix="fuego_xunta_", suffix=".pem", delete=False)
    with f:
        f.write(Path(certifi.where()).read_text())
        f.write("\n" + INTERMEDIO.read_text())
    return f.name


@functools.cache
def codigos_ine():
    """Nombre del concello tal como sale en el IRDI → código INE (padrón 2025)."""
    with open(RECURSOS / "irdi_concellos.csv", encoding="utf-8") as f:
        return {fila["concello"]: fila["codigo_ine"] for fila in csv.DictReader(f)}


# --- parseo --------------------------------------------------------------------

def _texto(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def actualizado(paxina):
    """«Actualizado o Sábado, 07 de Novembro de 2026 ás 09:48» → datetime con zona."""
    m = re.search(r"Actualizado o\s+[^,<]+,\s*(\d{1,2}) de (\w+) de (\d{4}) ás (\d{1,2}):(\d{2})", paxina)
    if not m:
        raise RuntimeError("IRDI: no encuentro «Actualizado o …» en la página")
    dia, mes, ano, h, mi = m.groups()
    if mes.lower() not in MESES:
        raise RuntimeError(f"IRDI: mes desconocido {mes!r}")
    return datetime(int(ano), MESES[mes.lower()], int(dia), int(h), int(mi), tzinfo=HORA)


def pestanas(paxina, base: date):
    """Pestañas de días («Sábado 07») → {"dia_1": fecha, ...}. La fecha se
    deduce del número de día, buscándolo desde la víspera de `base`."""
    dias = {}
    for pid, etiqueta in re.findall(r'<a href="#(dia_\d+)"[^>]*>([^<]+)</a>', paxina):
        n = re.search(r"(\d{1,2})\s*$", etiqueta.strip())
        if not n:
            raise RuntimeError(f"IRDI: pestaña sin día: {etiqueta!r}")
        for k in range(-1, 8):
            d = base + timedelta(days=k)
            if d.day == int(n.group(1)):
                dias[pid] = d
                break
        else:
            raise RuntimeError(f"IRDI: la pestaña {etiqueta!r} no cuadra con el {base}")
    if sorted(dias) != [f"dia_{k}" for k in range(1, DIAS + 1)]:
        raise RuntimeError(f"IRDI: pestañas inesperadas {sorted(dias)}")
    return dias


FILA = re.compile(
    r'<td headers="view-field-zona-table-column"[^>]*>(.*?)</td>\s*'
    r'<td headers="view-field-p50-table-column"[^>]*class="([^"]*)"[^>]*>(.*?)</td>', re.S)


def filas(tabla_html):
    """[(concello, nivel 1-5, nombre del nivel)] de un trozo de HTML de la tabla.
    El nivel se saca del texto; la clase CSS (irdi-…) solo si el texto no se reconoce."""
    out = []
    for zona, clase, texto in FILA.findall(tabla_html):
        nome = _texto(texto)
        nivel = NIVEIS.get(nome.lower())
        if nivel is None:
            m = re.search(r"\birdi-([a-z-]+)", clase)
            nivel = NIVEIS.get(m.group(1).replace("-", " ")) if m else None
        if nivel is None:
            raise RuntimeError(f"IRDI: nivel desconocido {nome!r} ({clase!r}) en {_texto(zona)!r}")
        out.append((_texto(zona), nivel, nome))
    return out


def html_ajax(datos: bytes):
    """HTML de la tabla en la respuesta de `views/ajax`: una lista de comandos
    de Drupal; va en el `data` del primer `insert` que no esté vacío."""
    try:
        comandos = json.loads(datos)
    except json.JSONDecodeError:
        raise RuntimeError(f"IRDI views/ajax: no es JSON ({datos[:80]!r})") from None
    trozos = [c["data"] for c in comandos if isinstance(c, dict) and c.get("command") == "insert"
              and isinstance(c.get("data"), str) and c["data"].strip()]
    if not trozos:
        raise RuntimeError("IRDI views/ajax: respuesta sin tabla")
    return trozos[0]


def comprobar(dia, todas):
    nombres = {f[0] for f in todas}
    if len(todas) != CONCELLOS or len(nombres) != CONCELLOS:
        raise RuntimeError(f"IRDI {dia}: {len(todas)} filas y {len(nombres)} concellos distintos; "
                           f"se esperaban {CONCELLOS}")


def a_csv(act, dias, tablas):
    """tablas: {"dia_1": [(concello, nivel, nombre)], ...} → CSV ordenado por día y código INE."""
    codigos = codigos_ine()
    sin = sorted({f[0] for t in tablas.values() for f in t} - set(codigos))
    if sin:
        log.warning("IRDI: concellos sin código INE: %s", ", ".join(sin))
    s = io.StringIO()
    w = csv.writer(s, lineterminator="\n")
    w.writerow(COLUMNAS)
    for dia in sorted(tablas):
        for nome, nivel, nivel_nome in sorted(tablas[dia], key=lambda f: (codigos.get(f[0], "99999"), f[0])):
            w.writerow((dias[dia].isoformat(), dia.removeprefix("dia_"), act.isoformat(timespec="minutes"),
                        codigos.get(nome, ""), nome, nivel, nivel_nome))
    return s.getvalue().encode()


def _tar(ficheros, mtime):
    """[(nombre, bytes)] en un tar.gz con fecha fija."""
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w", format=tarfile.PAX_FORMAT) as t:
        for nombre, datos in ficheros:
            i = tarfile.TarInfo(nombre)
            i.size, i.mtime = len(datos), int(mtime.timestamp())
            t.addfile(i, io.BytesIO(datos))
    return comprimir(b.getvalue())


# --- descarga ------------------------------------------------------------------

_SESION = requests.Session()


def _pide(metodo, url, registro, **kw):
    """GET o POST con el bundle de certificados, una pausa antes y hasta 3
    intentos con espera creciente. Apunta cada petición en `registro`."""
    for intento in range(1, INTENTOS + 1):
        time.sleep(PAUSA)
        try:
            r = _SESION.request(metodo, url, verify=_certificados(), timeout=TIEMPO,
                                headers={"User-Agent": USER_AGENT}, **kw)
            registro.append({"hora": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "metodo": metodo, "url": r.url, "cuerpo": kw.get("data"),
                             "http": r.status_code, "bytes": len(r.content),
                             "last_modified": r.headers.get("Last-Modified"), "user_agent": USER_AGENT})
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if intento == INTENTOS:
                raise
            log.warning("IRDI %s %s: %s; reintento en %d s", metodo, url, e, 10 * intento)
            time.sleep(10 * intento)


def _tabla(dia, registro, brutos):
    todas, primera = [], None
    for p in range(MAX_PAXINAS):
        r = _pide("POST", AJAX, registro, params={"_wrapper_format": "drupal_ajax", "page": p},
                  data={**AJAX_CUERPO, "view_args": dia})
        brutos.append((f"{dia}_p{p}.json", r.content))
        f = filas(html_ajax(r.content))
        todas += f
        primera = primera or len(f)
        if not f or len(f) < primera:
            break
    else:
        raise RuntimeError(f"IRDI {dia}: más de {MAX_PAXINAS} páginas")
    comprobar(dia, todas)
    return todas


def _clave_libre(lote, producto, nombre, fecha):
    """Si ya hay un fichero con ese nombre (la Xunta corrigió algo sin cambiar
    la hora de «Actualizado o»), se le añade la hora de la descarga."""
    clave = lote.clave(producto, nombre, fecha)
    if lote.almacen.existe(clave):
        base, ext = nombre.split(".", 1)
        clave = lote.clave(producto, f"{base}_{lote.inicio:%H%M%S}.{ext}", fecha)
    return clave


def _atrasado(last_modified, act):
    """El servidor a veces da la copia del día anterior de un JPG (medido el
    07/11/2026: irdi_d0.jpg alternaba entre la de las 08:47 de ese día y la
    del 06/11). Se toma por atrasado si es de más de 6 h antes de «Actualizado o»."""
    try:
        return parsedate_to_datetime(last_modified) < act - timedelta(hours=6)
    except (TypeError, ValueError):
        return False


def _mapas(lote, act, dias, registro):
    n = 0
    for k in range(MAPAS):
        url = MAPA.format(k)
        r = _pide("GET", url, registro)
        if not r.content.startswith(b"\xff\xd8"):
            raise RuntimeError(f"IRDI mapa d{k}: no es un JPG ({r.content[:40]!r})")
        if _atrasado(r.headers.get("Last-Modified"), act):
            log.warning("IRDI mapa d%d: copia atrasada (Last-Modified %s); no se guarda",
                        k, r.headers.get("Last-Modified"))
            continue
        valido = dias.get(f"dia_{k + 1}")
        nombre = f"irdi_{valido:%Y%m%d}_d{k}.jpg" if valido else f"irdi_d{k}.jpg"
        n += lote.guardar_si_cambia(f"xunta_irdi_d{k}", _clave_libre(lote, "irdi", nombre, lote.inicio),
                                    r.content, origen=url, licencia=LICENCIA, valido=f"{valido or ''}",
                                    last_modified=r.headers.get("Last-Modified") or "")
    return n


def irdi(lote):
    registro, brutos = [], []

    def fallo(motivo):
        # el bruto se guarda igual, para ver qué ha cambiado; el job falla y vigilancia avisa
        lote.guardar(lote.clave("irdi", f"irdi_fallo_{lote.inicio:%H%M%S}.tar.gz"),
                     _tar(brutos + [("peticiones.json", json.dumps(registro, indent=1).encode())],
                          lote.inicio),
                     origen=PAXINA, licencia=LICENCIA, motivo=str(motivo)[:200])

    pagina = _pide("GET", PAXINA, registro).content
    brutos.append(("pagina.html", pagina))
    try:
        act = actualizado(pagina.decode("utf-8"))
        dias = pestanas(pagina.decode("utf-8"), act.date())
    except RuntimeError as e:
        fallo(e)
        raise
    n = _mapas(lote, act, dias, registro)

    sello = f"{act:%Y%m%d%H%M}"
    if lote.almacen.existe(lote.clave("irdi", f"irdi_{sello}.csv", act)):
        log.info("IRDI actualizado %s: ya guardado", act)
        return n
    try:
        tablas = {dia: _tabla(dia, registro, brutos) for dia in sorted(dias)}
        # la página trae las 50 primeras filas de dia_1: tienen que ser las mismas
        # (la sesión guarda la cookie del balanceador, así que todo sale del mismo servidor)
        en_pagina = filas(pagina.decode("utf-8"))
        if en_pagina != tablas["dia_1"][:len(en_pagina)]:
            raise RuntimeError("IRDI: la tabla de views/ajax no cuadra con la de la página")
        fin = _pide("GET", PAXINA, registro).content
        brutos.append(("pagina_fin.html", fin))
        if actualizado(fin.decode("utf-8")) != act:
            raise RuntimeError("IRDI: se ha actualizado mientras se bajaba; la siguiente pasada lo recoge")
    except Exception as e:
        fallo(e)
        raise
    for dia, t in tablas.items():
        log.info("IRDI %s (%s): %s", dia, dias[dia], dict(Counter(f[2] for f in t)))

    extra = dict(licencia=LICENCIA, valido=f"{dias['dia_1']}/{dias[f'dia_{DIAS}']}",
                 actualizado=act.isoformat())
    clave = _clave_libre(lote, "irdi", f"irdi_{sello}.csv", act)
    if lote.guardar_si_cambia("xunta_irdi_tabla", clave, a_csv(act, dias, tablas), origen=AJAX, **extra):
        n += 1
        brutos.append(("peticiones.json", json.dumps(registro, indent=1).encode()))
        lote.guardar(_clave_libre(lote, "irdi", f"irdi_{sello}_bruto.tar.gz", act), _tar(brutos, act),
                     origen=f"{PAXINA} y {AJAX}", **extra)
    return n
