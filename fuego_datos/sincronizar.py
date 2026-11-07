"""Copia el bucket al disco y, solo después, libera el bucket.

Reglas, por este orden:
1. Todo lo que hay en el bucket y no está en el disco se baja, se comprueba
   su SHA-256 contra el de los metadatos y se escribe en cada destino.
2. Cada copia correcta se apunta en `.registro.sqlite` del destino.
3. Se borra del bucket un objeto solo si tiene más de RETENCION_DIAS días y
   está apuntado como copiado en TODOS los destinos configurados. Si un
   destino no está montado (disco desconectado), no se borra nada.
4. `estado/` no se copia ni se borra: es lo que usan los jobs para no guardar
   dos veces lo mismo.
5. El bucket se queda solo con lo imprescindible (R2 es gratis hasta 10 GB y
   el tope propio es 6 GB). Por defecto, RETENCION_DIAS (3): lo justo para que
   los jobs que miran hasta 2 días atrás (IFS) no vuelvan a bajar lo que ya
   hay. Más tiempo, aunque ya esté copiado (`RETENCION_MINIMA`):
   - el IFS a 0,25°, que el servidor de la web pide desde D-8;
   - el ERA5-Land, que el servidor pide desde el día 1 del mes de D-6 y el job
     mira hasta D-25;
   - los manifiestos, con los que `estado` sabe cuándo guardó cada job (EFFIS
     avisa a los 7 días).
6. La edad se cuenta desde la fecha del dato, la de la ruta (`.../AAAA/MM/DD/...`,
   hasta el final de ese día), y si la ruta no la lleva, desde la subida. Así
   un relleno de días antiguos no ocupa el bucket hasta que caduca la subida.
"""
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .almacen import sha256

log = logging.getLogger(__name__)

EXCLUIDOS = ("estado/",)
RETENCION_MINIMA = {"ecmwf/ifs_0p25/": 10, "era5land/": 40, "manifiestos/": 9}     # días
FECHA_EN_RUTA = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/")


def fecha_del_dato(o):
    """Final del día que lleva la ruta, o la subida si es anterior o no hay fecha."""
    m = FECHA_EN_RUTA.search(o.clave)
    if not m:
        return o.modificado
    fin = datetime(*map(int, m.groups()), tzinfo=timezone.utc) + timedelta(days=1)
    return min(fin, o.modificado)


class Destino:
    def __init__(self, ruta):
        self.ruta = Path(ruta).expanduser()
        self.ok = self.ruta.is_dir()
        self.db = None
        if self.ok:
            self.db = sqlite3.connect(self.ruta / ".registro.sqlite")
            self.db.execute("""CREATE TABLE IF NOT EXISTS copiados (
                clave TEXT PRIMARY KEY, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
                copiado TEXT NOT NULL)""")

    def tiene(self, clave):
        fila = self.db.execute("SELECT sha256, bytes FROM copiados WHERE clave=?", (clave,)).fetchone()
        if not fila:
            return False
        ruta = self.ruta / clave
        return ruta.is_file() and ruta.stat().st_size == fila[1]

    def escribir(self, clave, datos, huella):
        ruta = self.ruta / clave
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_name(ruta.name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(datos)
            f.flush()
            os.fsync(f.fileno())
        if sha256(tmp.read_bytes()) != huella:
            tmp.unlink()
            raise IOError(f"{clave}: la copia en {self.ruta} no coincide al releerla")
        os.replace(tmp, ruta)
        self.db.execute("INSERT OR REPLACE INTO copiados VALUES (?,?,?,?)",
                        (clave, huella, len(datos), datetime.now(timezone.utc).isoformat()))
        self.db.commit()


def sincronizar(almacen, rutas, retencion_dias=3, simulacion=False):
    destinos = [Destino(r) for r in rutas]
    for d in destinos:
        if not d.ok:
            log.warning("destino %s no disponible: se copia al resto y no se borra nada", d.ruta)
    vivos = [d for d in destinos if d.ok]
    if not vivos:
        raise SystemExit("Ningún destino disponible. Revisa DESTINO_ARCHIVO")

    objetos = [o for o in almacen.listar() if not o.clave.startswith(EXCLUIDOS)]
    copiados = errores = 0
    for o in objetos:
        faltan = [d for d in vivos if not d.tiene(o.clave)]
        if not faltan:
            continue
        if simulacion:
            log.info("copiaría %s (%.1f MB) a %d destino(s)", o.clave, o.bytes / 1e6, len(faltan))
            continue
        try:
            datos = almacen.leer(o.clave)
            huella = almacen.metadatos(o.clave).get("sha256")
            if huella and sha256(datos) != huella:
                raise IOError(f"{o.clave}: el SHA-256 descargado no coincide con el del bucket")
            huella = huella or sha256(datos)
            for d in faltan:
                d.escribir(o.clave, datos, huella)
            copiados += 1
        except Exception as e:
            errores += 1
            log.error("no se pudo copiar %s: %s", o.clave, e)

    ahora = datetime.now(timezone.utc)

    def caducado(o):
        dias = max([retencion_dias] + [n for p, n in RETENCION_MINIMA.items() if o.clave.startswith(p)])
        return fecha_del_dato(o) < ahora - timedelta(days=dias)

    borrables = []
    if all(d.ok for d in destinos) and not errores:
        borrables = [o.clave for o in objetos
                     if caducado(o) and all(d.tiene(o.clave) for d in destinos)]
    elif errores:
        log.warning("hubo %d errores de copia: esta vez no se borra nada del bucket", errores)
    if borrables and not simulacion:
        almacen.borrar(borrables)
    total = sum(o.bytes for o in objetos) / 1e9
    queda = total - sum(o.bytes for o in objetos if o.clave in set(borrables)) / 1e9
    log.info("%s: %d objetos (%.2f GB), %d copiados, %d errores, %d %s del bucket (más de %d días); "
             "%s %.2f GB", almacen, len(objetos), total, copiados, errores, len(borrables),
             "se borrarían" if simulacion else "borrados", retencion_dias,
             "quedarían" if simulacion else "quedan", queda)
    return {"objetos": len(objetos), "copiados": copiados, "errores": errores,
            "borrados": 0 if simulacion else len(borrables), "gb_quedan": round(queda, 2)}
