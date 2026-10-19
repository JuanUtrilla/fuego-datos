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


def test_lo_que_pide_el_servidor_se_queda_mas(tmp_path):
    a = _bucket(tmp_path)
    for clave, dias in (("era5land/h/1.nc", 30), ("era5land/h/2.nc", 41),
                        ("ecmwf/i/1.nc", 9), ("ecmwf/i/2.nc", 11)):
        a.guardar(clave, clave.encode(), {"sha256": sha256(clave.encode())})
        _envejecer(a, clave, dias)
    disco = tmp_path / "disco"
    disco.mkdir()
    sincronizar(a, [disco], retencion_dias=7)
    assert all((disco / c).exists() for c in ("era5land/h/1.nc", "era5land/h/2.nc", "ecmwf/i/1.nc"))
    assert a.existe("era5land/h/1.nc") and a.existe("ecmwf/i/1.nc")
    assert not a.existe("era5land/h/2.nc") and not a.existe("ecmwf/i/2.nc")


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


def test_relleno_antiguo_cuenta_por_la_fecha_del_dato(tmp_path):
    a = AlmacenLocal(tmp_path / "bucket")
    for clave in ("ecmwf/ifs/2024/05/24/a.nc", "era5land/h/2024/05/01/b.nc", "ecmwf/ifs/2099/01/01/c.nc"):
        a.guardar(clave, clave.encode(), {"sha256": sha256(clave.encode())})   # subidos hoy
    disco = tmp_path / "disco"
    disco.mkdir()
    r = sincronizar(a, [disco], retencion_dias=7)
    assert r["borrados"] == 2
    assert not a.existe("ecmwf/ifs/2024/05/24/a.nc") and not a.existe("era5land/h/2024/05/01/b.nc")
    assert a.existe("ecmwf/ifs/2099/01/01/c.nc")      # fecha futura: manda la subida
    assert (disco / "ecmwf/ifs/2024/05/24/a.nc").exists()
