"""Uso:

    python -m fuego_datos bajar ecmwf
    python -m fuego_datos bajar firms effis miteco
    python -m fuego_datos sincronizar [--simulacion]
    python -m fuego_datos estado [--max-horas 30]

`bajar` ejecuta cada job aunque falle otro, y termina con error si alguno
falló (así GitHub avisa por correo).
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

from . import almacen as alm
from .config import var

log = logging.getLogger("fuego_datos")


def _jobs():
    from .fuentes import aemet, ecmwf, era5land, icon_eu, incendios, siar
    # job → (fuente, función, horas máximas sin datos nuevos antes de avisar)
    return {
        "ecmwf": ("ecmwf", ecmwf.ejecutar, 18),
        "icon-eu": ("dwd", icon_eu.ejecutar, 12),
        "era5land": ("era5land", era5land.ejecutar, 72),
        "aemet-obs": ("aemet", aemet.observacion, 9),
        "aemet-pred": ("aemet", aemet.prediccion, 30),
        "aemet-productos": ("aemet", aemet.productos, 30),
        "firms": ("firms", incendios.firms, 30),
        "effis": ("effis", incendios.effis, 168),   # fuera de temporada cambia poco
        "miteco": ("miteco", incendios.miteco, 72),
        "siar": ("siar", siar.ejecutar, 30),
    }


def bajar(nombres):
    jobs = _jobs()
    desconocidos = set(nombres) - set(jobs)
    if desconocidos:
        raise SystemExit(f"jobs desconocidos: {', '.join(sorted(desconocidos))}. Hay: {', '.join(jobs)}")
    almacen = alm.abrir()
    log.info("guardando en %s", almacen)
    fallos = []
    for nombre in nombres:
        fuente, funcion, _ = jobs[nombre]
        lote = alm.Lote(almacen, fuente, nombre)
        try:
            n = funcion(lote)
            log.info("%s: %s nuevo(s)", nombre, n)
            # latido: el job ha terminado bien, haya o no datos nuevos
            almacen.guardar(f"estado/latido_{nombre}.json", json.dumps(
                {"fin": alm.ahora().isoformat(), "nuevos": n}).encode(), {})
        except (Exception, SystemExit):     # SystemExit: falta una clave; no para al resto
            log.exception("%s falló", nombre)
            fallos.append(nombre)
        finally:
            lote.cerrar()
    if fallos:
        log.error("fallaron: %s", ", ".join(fallos))
        return 1
    return 0


def rellenar_ifs(desde, hasta):
    """Como `bajar ecmwf`, pero para un tramo de fechas pasadas. No deja latido:
    no es un job programado y `estado` no lo vigila."""
    from .fuentes import ecmwf
    almacen = alm.abrir()
    lote = alm.Lote(almacen, "ecmwf", "ecmwf-relleno")
    try:
        n = ecmwf.rellenar(lote, desde, hasta)
        log.info("relleno IFS %s → %s: %d pasada(s) nuevas", desde, hasta, n)
        return 0
    except Exception:
        log.exception("relleno IFS %s → %s con fallos", desde, hasta)
        return 1
    finally:
        lote.cerrar()


def estado(margen_horas=0):
    """Para cada job: cuándo terminó bien por última vez (latido) y cuándo
    guardó datos nuevos (manifiesto). Error si alguno pasa de su límite."""
    almacen = alm.abrir()
    ahora = datetime.now(timezone.utc)
    datos = {}
    for o in almacen.listar("manifiestos/"):
        job = o.clave.rsplit("_", 1)[-1].removesuffix(".json")
        datos[job] = max(datos.get(job, o.modificado), o.modificado)

    def hace(t):
        return f"hace {(ahora - t) / timedelta(hours=1):5.1f} h" if t else "nunca"

    mal = []
    print(f"{'':4}{'job':17}{'último fin correcto':22}{'últimos datos nuevos':22}límite")
    for job, (_, _, horas) in _jobs().items():
        try:
            latido = datetime.fromisoformat(json.loads(almacen.leer(f"estado/latido_{job}.json"))["fin"])
        except Exception:
            latido = None
        t = datos.get(job)
        limite = timedelta(hours=horas + margen_horas)
        ok = bool(latido and t and ahora - latido <= limite and ahora - t <= limite)
        print(f"{'ok' if ok else 'MAL':4}{job:17}{hace(latido):22}{hace(t):22}{horas} h")
        if not ok:
            mal.append(job)
    return 1 if mal else 0


def main(argv=None):
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="fuego_datos")
    sub = p.add_subparsers(dest="orden", required=True)
    b = sub.add_parser("bajar")
    b.add_argument("jobs", nargs="+")
    s = sub.add_parser("sincronizar")
    s.add_argument("--simulacion", action="store_true", help="no copia ni borra; solo dice qué haría")
    ri = sub.add_parser("rellenar-ifs", help="pasadas antiguas de IFS 0,25° del archivo de AWS")
    ri.add_argument("desde", help="aaaa-mm-dd")
    ri.add_argument("hasta", help="aaaa-mm-dd (incluida)")
    e = sub.add_parser("estado")
    e.add_argument("--margen-horas", type=float, default=0)
    a = p.parse_args(argv)
    if a.orden == "bajar":
        return bajar(a.jobs)
    if a.orden == "rellenar-ifs":
        return rellenar_ifs(a.desde, a.hasta)
    if a.orden == "estado":
        return estado(a.margen_horas)
    from .sincronizar import sincronizar
    rutas = [r for r in var("DESTINO_ARCHIVO", obligatoria=True).split(":") if r]
    r = sincronizar(alm.abrir(), rutas, int(var("RETENCION_DIAS", "7")), a.simulacion)
    print(json.dumps(r))
    return 1 if r["errores"] else 0


if __name__ == "__main__":
    sys.exit(main())
