"""SiAR: red de estaciones agroclimáticas del Ministerio de Agricultura (MAPA).

Manual de la API: docs/siar_api_manual.md (versión 2.2, mayo de 2025).

Lo que deja el token que tenemos (consultado con Info/ACCESOS el 20/10/2026):
100 registros y 100 peticiones por minuto, 10.000 registros y 1.000 peticiones
al día, y nada anterior a hace un año. Un registro es una fila: en «Diarios»,
una estación y un día; en «Horarios», una estación y media hora (48 al día).

Por eso cada ejecución gasta un presupuesto de registros, sin repetir nunca un
dato ya bajado y empezando siempre por lo más reciente:

1. Diarios de todas las estaciones activas del dominio (máximas, mínimas,
   humedad mínima, racha de viento, lluvia: lo que pide el FWI). Primero ayer,
   luego hacia atrás hasta donde deja el token.
2. Con lo que sobra, semihorarios de las estaciones por orden de prioridad
   (repartidas por provincias), también de lo más reciente hacia atrás.

Lo bajado se apunta en `estado/siar_hecho.json.gz` (tipo → fecha → estaciones)
justo después de guardar cada fichero, así que si el job se corta no se pierde
ni se repite nada. Los días que la API devuelve vacíos se reintentan durante
una semana y después se dan por hechos (estación averiada o sin datos).

Licencia: el MAPA ofrece los datos gratis; falta confirmar por escrito el uso
comercial y la cita (ALTAS_APIS.md). Mientras, se cita la fuente.
"""
import gzip
import json
import logging
import re
import time

import requests
from datetime import date, timedelta

from ..almacen import ahora, comprimir
from ..config import ESTE, NORTE, OESTE, SUR, var
from ..red import SESION, TIEMPO

log = logging.getLogger(__name__)

BASE = "https://servicio.mapa.gob.es/siarapi/API/V1"
LIC = "Fuente: SiAR, Ministerio de Agricultura, Pesca y Alimentación"
ESTADO_HECHO = "estado/siar_hecho.json.gz"
ESTADO_ESTACIONES = "estado/siar_estaciones.json"
POR_MINUTO = 100            # registros por minuto que deja el token
POR_PETICION = 40           # estaciones por petición: con 40 va bien, con 100 la API da 500
PAUSA = 61                  # segundos entre peticiones de datos
DIAS_VENTANA = 364          # el token no deja pedir más atrás
DIAS_REINTENTO_VACIO = 7
MINUTOS = 130               # el workflow corta a los 150: parar antes y acabar bien
SEMIHORAS = 48
INTENTOS_403 = 3
PAUSA_403 = 120             # segundos antes de repetir un 403


class Cuota(Exception):
    """El token ya no deja más por hoy (o por este minuto)."""


class Bloqueo(Exception):
    """La API sigue contestando 403 después de varios intentos."""


class FueraDeVentana(Exception):
    """La fecha es anterior a la que deja pedir el token."""


def _pide(ruta, token, **params):
    """GET a la API. Devuelve la lista `datos`. La API contesta con HTTP 200 y
    el problema en `MensajeRespuesta` (cuota, fechas fuera de rango...)."""
    url = f"{BASE}/{ruta}"
    for intento in range(1, INTENTOS_403 + 1):
        try:
            r = SESION.get(url, params={"token": token, **params}, timeout=TIEMPO)
            r.raise_for_status()
            break
        except requests.RequestException as e:
            # el token va en la URL: que no salga nunca en el registro (en GitHub es público)
            estado = getattr(e.response, "status_code", None)
            msg = (f"SiAR {ruta}: {type(e).__name__} {estado or ''} "
                   f"(Id={params.get('Id')}, {params.get('FechaInicial', '')})")
            if estado != 403:
                raise RuntimeError(msg) from None
            # 403 suelto con el token y la misma petición válidos (23/10/2026): se repite
            if intento == INTENTOS_403:
                raise Bloqueo(msg) from None
            log.warning("SiAR: 403, se repite en %d s (intento %d/%d)", PAUSA_403, intento, INTENTOS_403)
            time.sleep(PAUSA_403)
    d = r.json()
    msg = d.get("MensajeRespuesta")
    if msg:
        if re.search(r"rebasar|máximo|maximo|límite|limite", msg, re.I):
            raise Cuota(msg)
        if re.search(r"token", msg, re.I):
            raise SystemExit(f"SiAR: el token no vale ({msg}). Hay que pedir otro (ALTAS_APIS.md)")
        if re.search(r"Fecha M[ií]nima", msg, re.I):
            return None
        raise RuntimeError(f"SiAR {ruta}: {msg}")
    return d.get("datos") or []


def _grados(dms):
    """'391520000N' → 39.2556 (DD MM SS sss y hemisferio)."""
    s, h = dms[:-1], dms[-1]
    g = int(s[:-7]) + int(s[-7:-5]) / 60 + int(s[-5:]) / 1000 / 3600
    return -g if h in "WS" else g


def estaciones(almacen, token):
    """Estaciones activas dentro del dominio, por orden de prioridad. La lista
    gasta unos 635 registros: se guarda y se renueva cada semana."""
    try:
        e = json.loads(almacen.leer(ESTADO_ESTACIONES))
        if date.fromisoformat(e["fecha"]) > date.today() - timedelta(days=7):
            return e["estaciones"]
    except Exception:
        pass
    todas = _pide("Info/ESTACIONES", token)
    time.sleep(PAUSA)                   # la lista se come la cuota de este minuto
    hoy = date.today().isoformat()
    sel = []
    for x in todas:
        lat, lon = _grados(x["Latitud"]), _grados(x["Longitud"])
        if not (SUR <= lat <= NORTE and OESTE <= lon <= ESTE):
            continue                                   # Canarias, fuera del dominio
        if x.get("Fecha_Baja") and x["Fecha_Baja"][:10] <= hoy:
            continue
        sel.append({"codigo": x["Codigo"], "nombre": x["Estacion"], "provincia": x.get("IdProvincia"),
                    "lat": round(lat, 5), "lon": round(lon, 5), "altitud": x.get("Altitud"),
                    "instalacion": (x.get("Fecha_Instalacion") or "")[:10], "red": x.get("Red_Estacion")})
    # prioridad: una por provincia en cada vuelta, para repartir por el territorio
    por_prov = {}
    for s in sorted(sel, key=lambda s: s["codigo"]):
        por_prov.setdefault(s["provincia"], []).append(s)
    orden = []
    while any(por_prov.values()):
        for p in sorted(por_prov, key=str):
            if por_prov[p]:
                orden.append(por_prov[p].pop(0))
    almacen.guardar(ESTADO_ESTACIONES, json.dumps(
        {"fecha": hoy, "estaciones": orden, "lista_completa": todas}, ensure_ascii=False).encode(), {})
    log.info("SiAR: %d estaciones activas en el dominio (de %d)", len(orden), len(todas))
    return orden


def _leer_hecho(almacen):
    try:
        return json.loads(gzip.decompress(almacen.leer(ESTADO_HECHO)))
    except Exception:
        return {"Diarios": {}, "Horarios": {}}


def _guardar_hecho(almacen, hecho):
    almacen.guardar(ESTADO_HECHO, gzip.compress(json.dumps(hecho, separators=(",", ":")).encode()), {})


def _pendientes(hecho, tipo, codigos, desde, hasta):
    """(fecha, estaciones sin bajar) de la más reciente a la más antigua."""
    d = hasta
    while d >= desde:
        f = d.isoformat()
        hechas = set(hecho[tipo].get(f, []))
        faltan = [c for c in codigos if c not in hechas]
        if faltan:
            yield d, faltan
        d -= timedelta(days=1)


def ejecutar(lote):
    token = var("SIAR_TOKEN", obligatoria=True)
    almacen = lote.almacen
    presupuesto = int(var("SIAR_REGISTROS_DIA", "9000"))
    para_diarios = int(var("SIAR_REGISTROS_DIARIOS", "3000"))

    acc = _pide("Info/ACCESOS", token)[0]
    libre = acc["MaxRegistrosDia"] - acc["RegistrosAcumuladosDia"] - 700   # margen para la lista
    presupuesto = max(0, min(presupuesto, libre))
    por_minuto = min(POR_MINUTO, acc["MaxRegistrosMinuto"])
    log.info("SiAR: cuota de hoy %d/%d registros usados; esta vez hasta %d",
             acc["RegistrosAcumuladosDia"], acc["MaxRegistrosDia"], presupuesto)

    est = estaciones(almacen, token)
    codigos = [e["codigo"] for e in est]
    hoy = date.today()
    hasta, desde = hoy - timedelta(days=1), hoy - timedelta(days=DIAS_VENTANA)
    hecho = _leer_hecho(almacen)
    gastado, n = 0, 0
    fin = time.monotonic() + 60 * int(var("SIAR_MINUTOS", str(MINUTOS)))

    def apunta(tipo, f, cods):
        hecho[tipo].setdefault(f, [])
        hecho[tipo][f] = sorted(set(hecho[tipo][f]) | set(cods))

    def bajar(tipo, d, cods):
        """Una petición: las estaciones `cods` el día `d`. Guarda y apunta."""
        nonlocal gastado, n
        if time.monotonic() > fin:
            raise Cuota("tiempo del workflow")
        datos = _pide(f"Datos/{tipo}/ESTACION", token, Id=cods,
                      FechaInicial=d.isoformat(), FechaFinal=d.isoformat())
        time.sleep(PAUSA)
        if datos is None:
            raise FueraDeVentana(d)
        gastado += len(datos)
        con = {x["Estacion"] for x in datos}
        viejo = d <= hoy - timedelta(days=DIAS_REINTENTO_VACIO)
        listas = [c for c in cods if c in con or viejo]
        if datos:
            f = d.isoformat()
            nombre = f"siar_{tipo.lower()}_{d:%Y%m%d}_{ahora():%H%M%S}_{cods[0]}.json.gz"
            lote.guardar(lote.clave(tipo.lower(), nombre, fecha=d),
                         comprimir(json.dumps(datos, ensure_ascii=False).encode()),
                         origen=f"{BASE}/Datos/{tipo}/ESTACION?Id={'&Id='.join(cods)}&FechaInicial={f}&FechaFinal={f}",
                         licencia=LIC, registros=len(datos))
            n += 1
        apunta(tipo, d.isoformat(), listas)
        _guardar_hecho(almacen, hecho)     # justo después de guardar: si se corta, no se repite

    try:
        # 1. diarios de todas, de ayer hacia atrás
        tope = min(para_diarios, presupuesto)
        try:
            for d, faltan in _pendientes(hecho, "Diarios", codigos, desde, hasta):
                lote_max = min(por_minuto, POR_PETICION)
                for i in range(0, len(faltan), lote_max):
                    trozo = faltan[i:i + min(lote_max, tope - gastado)]
                    if not trozo:
                        break
                    bajar("Diarios", d, trozo)
                if gastado >= tope:
                    break
        except FueraDeVentana as e:
            log.info("SiAR diarios: el token no deja pedir el %s; hasta ahí llega la ventana", e)
        log.info("SiAR diarios: %d registros", gastado)
        # 2. semihorarios con lo que queda, por prioridad
        cabe = max(1, por_minuto // SEMIHORAS)                    # 2 estaciones-día por petición
        try:
            for d, faltan in _pendientes(hecho, "Horarios", codigos, desde, hasta):
                for i in range(0, len(faltan), cabe):
                    if presupuesto - gastado < SEMIHORAS * cabe:
                        break
                    bajar("Horarios", d, faltan[i:i + cabe])
                if presupuesto - gastado < SEMIHORAS * cabe:
                    break
        except FueraDeVentana as e:
            log.info("SiAR semihorarios: el token no deja pedir el %s", e)
    except Cuota as e:
        log.warning("SiAR: se acabó la cuota (%s). Se sigue mañana", e)
    except Bloqueo as e:
        if n == 0:
            raise RuntimeError(str(e)) from None        # sin nada nuevo, que el job falle y se vea
        log.warning("SiAR: la API sigue dando 403 (%s). Se para con lo bajado y se sigue mañana", e)
    log.info("SiAR: %d registros en %d ficheros", gastado, n)
    return n
