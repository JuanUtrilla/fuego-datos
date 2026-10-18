import json

from fuego_datos.almacen import AlmacenLocal, Lote, comprimir, meta_a_s3, meta_de_s3, sha256


def test_meta_s3_solo_ascii_y_reversible():
    meta = {"licencia": "CC BY 4.0. © Unión Europea, Copernicus EMS - EFFIS",
            "origen": "https://firms.modaps.eosdis.nasa.gov/api/area/csv/<MAP_KEY>/x/-10,35,5,44/1",
            "sha256": sha256(b"x"), "pct": "100%"}
    s3 = meta_a_s3(meta)
    assert all(v.isascii() for v in s3.values())
    assert s3["sha256"] == meta["sha256"]
    assert meta_de_s3(s3) == meta


def test_guardar_leer_listar_borrar(tmp_path):
    a = AlmacenLocal(tmp_path)
    a.guardar("x/a.bin", b"hola", {"sha256": sha256(b"hola")})
    assert a.existe("x/a.bin")
    assert a.leer("x/a.bin") == b"hola"
    assert a.metadatos("x/a.bin")["sha256"] == sha256(b"hola")
    assert [o.clave for o in a.listar()] == ["x/a.bin"]      # .meta/ no aparece
    a.borrar(["x/a.bin"])
    assert not a.existe("x/a.bin") and list(a.listar()) == []


def test_lote_escribe_manifiesto(tmp_path):
    a = AlmacenLocal(tmp_path)
    lote = Lote(a, "prueba", "job")
    clave = lote.clave("prod", "f.txt")
    lote.guardar(clave, b"abc", origen="aqui")
    man = lote.cerrar()
    cuerpo = json.loads(a.leer(man))
    assert cuerpo["ficheros"][0]["clave"] == clave
    assert cuerpo["ficheros"][0]["sha256"] == sha256(b"abc")


def test_lote_sin_ficheros_no_deja_manifiesto(tmp_path):
    assert Lote(AlmacenLocal(tmp_path), "prueba", "job").cerrar() is None


def test_guardar_si_cambia(tmp_path):
    a = AlmacenLocal(tmp_path)
    lote = Lote(a, "prueba", "job")
    assert lote.guardar_si_cambia("p", "prueba/p/1.bin", b"v1")
    assert not lote.guardar_si_cambia("p", "prueba/p/2.bin", b"v1")   # mismo contenido
    assert lote.guardar_si_cambia("p", "prueba/p/3.bin", b"v2")
    assert a.existe("prueba/p/1.bin") and not a.existe("prueba/p/2.bin")


def test_comprimir_es_determinista():
    assert comprimir(b"x" * 1000) == comprimir(b"x" * 1000)
