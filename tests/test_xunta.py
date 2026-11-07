"""IRDI de la Xunta con las respuestas reales del 07/11/2026 (tests/datos_irdi:
la página y los 28 JSON de views/ajax) y un servidor falso, sin red."""
import csv
import gzip
import io
import json
import tarfile
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from fuego_datos.almacen import AlmacenLocal, Lote
from fuego_datos.fuentes import xunta

DATOS = Path(__file__).parent / "datos_irdi"


def _lee(nombre):
    return gzip.decompress((DATOS / f"{nombre}.gz").read_bytes())


PAGINA = _lee("pagina.html")
AJAX = {(d, p): _lee(f"dia_{d}_p{p}.json") for d in range(1, 5) for p in range(7)}
VACIA = _lee("vacia.json")
JPG = b"\xff\xd8\xff\xe0" + b"x" * 100


class Respuesta:
    def __init__(self, contenido, url, last_modified="Sat, 07 Nov 2026 08:47:10 GMT"):
        self.content, self.url, self.status_code = contenido, url, 200
        self.headers = {"Last-Modified": last_modified}


class XuntaFalsa:
    """Sirve la página, los JSON y unos JPG falsos. Con `despues` = k, la
    página cambia de hora a partir de la petición k + 1 (para simular una actualización)."""

    def __init__(self, ajax=AJAX, pagina=PAGINA):
        self.ajax, self.pagina, self.pedidos, self.despues = ajax, pagina, [], None
        self.d0_atrasado = False

    def __call__(self, metodo, url, registro, params=None, data=None, **kw):
        self.pedidos.append((metodo, url, (params or {}).get("page"), (data or {}).get("view_args")))
        registro.append({"metodo": metodo, "url": url})
        if url == xunta.PAXINA:
            pag = self.pagina
            if self.despues is not None and len(self.pedidos) > self.despues:
                pag = pag.replace("ás 09:48".encode(), "ás 10:15".encode())
            return Respuesta(pag, url)
        if url == xunta.AJAX:
            assert data["view_name"] == "tabla_irdi" and "page" not in data
            d = int(data["view_args"].removeprefix("dia_"))
            return Respuesta(self.ajax.get((d, params["page"]), VACIA), url)
        if url.endswith("d0.jpg") and self.d0_atrasado:
            return Respuesta(JPG + b"viejo", url, "Fri, 06 Nov 2026 08:46:58 GMT")
        return Respuesta(JPG + url[-5:].encode(), url)


@pytest.fixture
def servidor(monkeypatch):
    s = XuntaFalsa()
    monkeypatch.setattr(xunta, "_pide", s)
    return s


def _tabla(d):
    return [f for p in range(7) for f in xunta.filas(xunta.html_ajax(AJAX[(d, p)]))]


def test_parseo_respuestas_reales():
    pag = PAGINA.decode()
    act = xunta.actualizado(pag)
    assert act.isoformat() == "2026-11-07T09:48:00+01:00"
    dias = xunta.pestanas(pag, act.date())
    assert dias == {"dia_1": date(2026, 11, 7), "dia_2": date(2026, 11, 8),
                    "dia_3": date(2026, 11, 9), "dia_4": date(2026, 11, 10)}
    tablas = {f"dia_{d}": _tabla(d) for d in range(1, 5)}
    for dia, t in tablas.items():
        xunta.comprobar(dia, t)
    # lo que se vio en la web el 07/11/2026
    assert [dict(sorted(Counter(f[2] for f in tablas[f"dia_{d}"]).items()))
            for d in range(1, 5)] == [{"Baixo": 302, "Moderado": 11},
                                      {"Alto": 5, "Baixo": 278, "Moderado": 30},
                                      {"Baixo": 312, "Moderado": 1}, {"Baixo": 313}]
    assert xunta.filas(xunta.html_ajax(VACIA)) == []
    filas = list(csv.DictReader(io.StringIO(xunta.a_csv(act, dias, tablas).decode())))
    assert len(filas) == 4 * 313
    assert all(f["codigo_ine"] for f in filas)
    assert len({f["codigo_ine"] for f in filas}) == 313
    assert filas[0] == {"fecha_validez": "2026-11-07", "dia": "1", "actualizado": "2026-11-07T09:48+01:00",
                        "codigo_ine": "15001", "concello": "Abegondo", "nivel": "1", "nivel_nome": "Baixo"}


def test_pestanas_cambio_de_mes():
    p = "".join(f'<a href="#dia_{k}" data-toggle="tab">X {d:02d}</a>' for k, d in enumerate((30, 31, 1, 2), 1))
    assert xunta.pestanas(p, date(2026, 10, 30))["dia_3"] == date(2026, 11, 1)


def test_niveles_por_texto_o_clase():
    fila = ('<td headers="view-field-zona-table-column" class="x">O Grove </td>'
            '<td headers="view-field-p50-table-column" class="views-field irdi-{}">{}</td>')
    assert xunta.filas(fila.format("moi-alto", "Moi alto"))[0][1:] == (4, "Moi alto")
    assert xunta.filas(fila.format("x", "Extremo"))[0][1] == 5
    assert xunta.filas(fila.format("extremo", "?"))[0][1] == 5
    with pytest.raises(RuntimeError, match="nivel desconocido"):
        xunta.filas(fila.format("raro", "Raro"))


def test_job_guarda_y_no_repite(servidor, tmp_path):
    almacen = AlmacenLocal(tmp_path)
    lote = Lote(almacen, "xunta", "irdi")
    assert xunta.irdi(lote) == 5                     # tabla + 4 mapas
    posts = [(p, d) for m, u, p, d in servidor.pedidos if m == "POST"]
    assert posts == [(p, f"dia_{d}") for d in range(1, 5) for p in range(7)]    # 28, sin pedir la 8.ª
    lote.cerrar()
    claves = sorted(o.clave for o in almacen.listar("xunta/"))
    base = "xunta/irdi/2026/11/07/irdi_202611070948"
    assert f"{base}.csv" in claves and f"{base}_bruto.tar.gz" in claves
    assert sum(c.endswith(".jpg") for c in claves) == 4
    assert any(c.endswith("irdi_20261110_d3.jpg") for c in claves)
    with tarfile.open(fileobj=io.BytesIO(almacen.leer(f"{base}_bruto.tar.gz"))) as t:
        nombres = t.getnames()
        assert len(nombres) == 1 + 28 + 1 + 1        # página, JSON, página final, peticiones
        assert t.extractfile("dia_2_p6.json").read() == AJAX[(2, 6)]
    assert len(almacen.leer(f"{base}.csv").decode().splitlines()) == 1 + 4 * 313

    # misma actualización: solo la página y los mapas, sin volver a pedir la tabla
    servidor.pedidos.clear()
    lote = Lote(almacen, "xunta", "irdi")
    assert xunta.irdi(lote) == 0
    assert lote.cerrar() is None
    assert [m for m, *_ in servidor.pedidos] == ["GET"] * 5
    assert sorted(o.clave for o in almacen.listar("xunta/")) == claves


def test_falta_un_dia_entero(monkeypatch, tmp_path):
    s = XuntaFalsa(ajax={k: v for k, v in AJAX.items() if k[0] != 3})
    monkeypatch.setattr(xunta, "_pide", s)
    almacen = AlmacenLocal(tmp_path)
    lote = Lote(almacen, "xunta", "irdi")
    with pytest.raises(RuntimeError, match="dia_3: 0 filas"):
        xunta.irdi(lote)
    man = json.loads(almacen.leer(lote.cerrar()))
    nombres = [f["clave"].rsplit("/", 1)[1] for f in man["ficheros"]]
    assert sum(n.endswith(".jpg") for n in nombres) == 4          # los mapas se guardan igual
    assert any(n.startswith("irdi_fallo_") for n in nombres)
    assert not any(n.endswith(".csv") for n in nombres)


def test_actualizada_a_mitad(monkeypatch, tmp_path):
    s = XuntaFalsa()
    s.despues = 10                                   # a partir de la 11.ª petición, otra hora
    monkeypatch.setattr(xunta, "_pide", s)
    almacen = AlmacenLocal(tmp_path)
    with pytest.raises(RuntimeError, match="mientras se bajaba"):
        xunta.irdi(Lote(almacen, "xunta", "irdi"))
    assert not any(o.clave.endswith(".csv") for o in almacen.listar("xunta/"))


def test_jpg_atrasado_no_se_guarda(servidor, tmp_path):
    servidor.d0_atrasado = True
    almacen = AlmacenLocal(tmp_path)
    assert xunta.irdi(Lote(almacen, "xunta", "irdi")) == 4      # tabla + d1..d3
    assert not any("_d0" in o.clave for o in almacen.listar("xunta/"))


def test_tabla_que_no_cuadra_con_la_pagina(monkeypatch, tmp_path):
    ajax = dict(AJAX)
    ajax[(1, 0)] = AJAX[(1, 0)].replace(b"irdi-baixo\\u0022\\u003EBaixo", b"irdi-alto\\u0022\\u003EAlto", 1)
    assert ajax[(1, 0)] != AJAX[(1, 0)]
    monkeypatch.setattr(xunta, "_pide", XuntaFalsa(ajax=ajax))
    with pytest.raises(RuntimeError, match="no cuadra"):
        xunta.irdi(Lote(AlmacenLocal(tmp_path), "xunta", "irdi"))


def test_certificados_incluyen_el_intermedio():
    texto = Path(xunta._certificados()).read_text()
    assert xunta.INTERMEDIO.read_text().split("-----BEGIN")[1] in texto
    assert texto.count("BEGIN CERTIFICATE") > 100
