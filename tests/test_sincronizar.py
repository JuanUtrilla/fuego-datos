import os
import time

from fuego_datos.almacen import AlmacenLocal, sha256
from fuego_datos.sincronizar import sincronizar


def _envejecer(almacen, clave, dias):
    t = time.time() - dias * 86400
    os.utime(almacen.raiz / clave, (t, t))


def _bucket(tmp_path):
    a = AlmacenLocal(tmp_path / "bucket")
    for clave, dias in (("f/viejo.bin", 10), ("f/nuevo.bin", 1)):
        datos = clave.encode() * 100
        a.guardar(clave, datos, {"sha256": sha256(datos)})
        _envejecer(a, clave, dias)
    a.guardar("estado/x.json", b"{}", {})
    _envejecer(a, "estado/x.json", 30)
    return a


def test_copia_todo_y_borra_solo_lo_viejo(tmp_path):
    a = _bucket(tmp_path)
    disco = tmp_path / "disco"
    disco.mkdir()
    r = sincronizar(a, [disco], retencion_dias=7)
    assert r == {"objetos": 2, "copiados": 2, "errores": 0, "borrados": 1}
    assert (disco / "f/viejo.bin").read_bytes() == b"f/viejo.bin" * 100
    assert not a.existe("f/viejo.bin")          # copiado y viejo: fuera
    assert a.existe("f/nuevo.bin")              # copiado pero reciente: se queda
    assert a.existe("estado/x.json")            # estado/ nunca se toca
    assert not (disco / "estado").exists()


def test_disco_desconectado_no_borra(tmp_path):
    a = _bucket(tmp_path)
    disco = tmp_path / "disco"
    disco.mkdir()
    r = sincronizar(a, [disco, tmp_path / "no_montado"], retencion_dias=7)
    assert r["copiados"] == 2 and r["borrados"] == 0
    assert a.existe("f/viejo.bin")


def test_huella_distinta_no_copia_ni_borra(tmp_path):
    a = _bucket(tmp_path)
    a.guardar("f/roto.bin", b"contenido", {"sha256": "0" * 64})
    _envejecer(a, "f/roto.bin", 10)
    disco = tmp_path / "disco"
    disco.mkdir()
    r = sincronizar(a, [disco], retencion_dias=7)
    assert r["errores"] == 1 and r["borrados"] == 0
    assert not (disco / "f/roto.bin").exists()
    assert a.existe("f/viejo.bin")


def test_simulacion_no_toca_nada(tmp_path):
    a = _bucket(tmp_path)
    disco = tmp_path / "disco"
    disco.mkdir()
    r = sincronizar(a, [disco], retencion_dias=7, simulacion=True)
    assert r["copiados"] == 0 and r["borrados"] == 0
    assert a.existe("f/viejo.bin") and not (disco / "f").exists()


def test_segunda_pasada_no_vuelve_a_copiar(tmp_path):
    a = _bucket(tmp_path)
    disco = tmp_path / "disco"
    disco.mkdir()
    sincronizar(a, [disco], retencion_dias=7)
    r = sincronizar(a, [disco], retencion_dias=7)
    assert r["copiados"] == 0
