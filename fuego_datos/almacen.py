"""Dónde se guardan los ficheros: el bucket privado de R2 o una carpeta local.

Las dos clases tienen la misma interfaz, así que las fuentes y la
sincronización no saben cuál están usando. La carpeta local sirve para probar
sin bucket.

Cada objeto lleva su huella SHA-256 en los metadatos (`sha256`). La
sincronización la usa para comprobar que la copia del disco es idéntica.

Organización de claves:
    <fuente>/<producto>/<aaaa>/<mm>/<dd>/<fichero>    los datos
    manifiestos/<fuente>/<aaaa>/<mm>/<dd>/<hhmmss>_<job>.json
    estado/<producto>.json                             última huella guardada
"""
import gzip
import hashlib
import json
import logging
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import RAIZ, var

log = logging.getLogger(__name__)


def sha256(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def ahora():
    return datetime.now(timezone.utc)


@dataclass
class Objeto:
    clave: str
    bytes: int
    modificado: datetime


class AlmacenLocal:
    """Carpeta local con la misma interfaz que el bucket. Los metadatos van en
    ficheros aparte bajo `.meta/`."""

    def __init__(self, raiz):
        self.raiz = Path(raiz).resolve()
        self.raiz.mkdir(parents=True, exist_ok=True)

    def __repr__(self):
        return f"carpeta local {self.raiz}"

    def _ruta(self, clave):
        return self.raiz / clave

    def _meta(self, clave):
        return self.raiz / ".meta" / f"{clave}.json"

    def guardar(self, clave, datos: bytes, meta: dict):
        ruta = self._ruta(clave)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_name(ruta.name + ".tmp")
        tmp.write_bytes(datos)
        os.replace(tmp, ruta)
        m = self._meta(clave)
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps(meta))

    def existe(self, clave):
        return self._ruta(clave).is_file()

    def leer(self, clave) -> bytes:
        return self._ruta(clave).read_bytes()

    def metadatos(self, clave) -> dict:
        m = self._meta(clave)
        return json.loads(m.read_text()) if m.is_file() else {}

    def listar(self, prefijo=""):
        base = self.raiz
        for ruta in sorted(base.rglob("*")):
            if not ruta.is_file() or ruta.name.endswith(".tmp"):
                continue
            clave = ruta.relative_to(base).as_posix()
            if clave.startswith(".meta/") or not clave.startswith(prefijo):
                continue
            st = ruta.stat()
            yield Objeto(clave, st.st_size, datetime.fromtimestamp(st.st_mtime, timezone.utc))

    def borrar(self, claves):
        for c in claves:
            self._ruta(c).unlink(missing_ok=True)
            self._meta(c).unlink(missing_ok=True)


class AlmacenR2:
    """Bucket de Cloudflare R2 a través de su API compatible con S3."""

    def __init__(self, cuenta, clave_id, secreto, bucket):
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        self.s3 = boto3.client(
            "s3", endpoint_url=f"https://{cuenta}.r2.cloudflarestorage.com",
            aws_access_key_id=clave_id, aws_secret_access_key=secreto,
            region_name="auto",
            config=Config(retries={"max_attempts": 8, "mode": "adaptive"},
                          connect_timeout=20, read_timeout=300))

    def __repr__(self):
        return f"bucket R2 {self.bucket}"

    def guardar(self, clave, datos: bytes, meta: dict):
        tipo = mimetypes.guess_type(clave)[0] or "application/octet-stream"
        self.s3.put_object(Bucket=self.bucket, Key=clave, Body=datos, ContentType=tipo,
                           Metadata={k: str(v) for k, v in meta.items()})

    def existe(self, clave):
        from botocore.exceptions import ClientError
        try:
            self.s3.head_object(Bucket=self.bucket, Key=clave)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def leer(self, clave) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=clave)["Body"].read()

    def metadatos(self, clave) -> dict:
        return self.s3.head_object(Bucket=self.bucket, Key=clave).get("Metadata", {})

    def listar(self, prefijo=""):
        pag = self.s3.get_paginator("list_objects_v2")
        for pagina in pag.paginate(Bucket=self.bucket, Prefix=prefijo):
            for o in pagina.get("Contents", []):
                yield Objeto(o["Key"], o["Size"], o["LastModified"])

    def borrar(self, claves):
        claves = list(claves)
        for i in range(0, len(claves), 1000):
            lote = [{"Key": c} for c in claves[i:i + 1000]]
            r = self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": lote, "Quiet": True})
            if r.get("Errors"):
                raise RuntimeError(f"R2 no pudo borrar {len(r['Errors'])} objetos: {r['Errors'][:3]}")


def abrir():
    """El bucket si están las variables de R2; si no, la carpeta local."""
    cuenta, clave_id = var("R2_ACCOUNT_ID"), var("R2_ACCESS_KEY_ID")
    secreto, bucket = var("R2_SECRET_ACCESS_KEY"), var("R2_BUCKET")
    if cuenta and clave_id and secreto and bucket:
        return AlmacenR2(cuenta, clave_id, secreto, bucket)
    if var("EXIGIR_R2"):
        # En GitHub Actions: sin bucket, lo descargado se perdería con el runner
        raise SystemExit("Faltan las variables de R2 y EXIGIR_R2 está activo")
    ruta = Path(var("ALMACEN_LOCAL", "datos_locales"))
    if not ruta.is_absolute():
        ruta = RAIZ / ruta
    return AlmacenLocal(ruta)


class Lote:
    """Lo que guarda un job: los ficheros y, al cerrar, su manifiesto.

    El manifiesto lista cada fichero con su huella, tamaño y origen. El
    servidor lo usa para saber qué hay nuevo, y es la prueba de que el job
    terminó: si un job muere a medias, no deja manifiesto.
    """

    def __init__(self, almacen, fuente, job):
        self.almacen, self.fuente, self.job = almacen, fuente, job
        self.inicio = ahora()
        self.ficheros = []

    def clave(self, producto, nombre, fecha=None):
        f = fecha or self.inicio
        return f"{self.fuente}/{producto}/{f:%Y/%m/%d}/{nombre}"

    def guardar(self, clave, datos: bytes, origen=None, **extra):
        h = sha256(datos)
        meta = {"sha256": h, "origen": origen or "", **extra}
        self.almacen.guardar(clave, datos, meta)
        self.ficheros.append({"clave": clave, "sha256": h, "bytes": len(datos),
                              "origen": origen, **extra})
        log.info("guardado %s (%.1f MB)", clave, len(datos) / 1e6)
        return h

    def guardar_si_cambia(self, producto_estado, clave, datos: bytes, origen=None, **extra):
        """Guarda solo si el contenido difiere del último guardado de ese
        producto. Para fuentes que se sobrescriben en el mismo sitio (el parte
        del MITECO, los mapas de AEMET) y se consultan más a menudo de lo que
        cambian."""
        h = sha256(datos)
        clave_estado = f"estado/{producto_estado}.json"
        try:
            previo = json.loads(self.almacen.leer(clave_estado))
        except Exception:
            previo = {}
        if previo.get("sha256") == h:
            log.info("sin cambios: %s (igual que %s)", producto_estado, previo.get("clave"))
            return False
        self.guardar(clave, datos, origen, **extra)
        self.almacen.guardar(clave_estado, json.dumps(
            {"sha256": h, "clave": clave, "fecha": ahora().isoformat()}).encode(), {})
        return True

    def cerrar(self):
        if not self.ficheros:
            log.info("%s/%s: nada nuevo que guardar", self.fuente, self.job)
            return None
        clave = f"manifiestos/{self.fuente}/{self.inicio:%Y/%m/%d/%H%M%S}_{self.job}.json"
        cuerpo = {"fuente": self.fuente, "job": self.job,
                  "inicio": self.inicio.isoformat(), "fin": ahora().isoformat(),
                  "ficheros": self.ficheros}
        datos = json.dumps(cuerpo, indent=1, ensure_ascii=False).encode()
        self.almacen.guardar(clave, datos, {"sha256": sha256(datos)})
        log.info("manifiesto %s: %d ficheros, %.1f MB", clave, len(self.ficheros),
                 sum(f["bytes"] for f in self.ficheros) / 1e6)
        return clave


def comprimir(datos: bytes) -> bytes:
    """gzip con fecha fija, para que el mismo contenido dé la misma huella."""
    return gzip.compress(datos, compresslevel=9, mtime=0)
