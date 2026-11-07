"""Índices autonómicos con respuestas reales del 07/11/2026 (tests/datos_ccaa,
recortadas) y un servidor falso, sin red."""
import gzip
import io
import json
import tarfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from fuego_datos import __main__ as principal
from fuego_datos.almacen import AlmacenLocal, Lote
from fuego_datos.fuentes import ccaa

DATOS = Path(__file__).parent / "datos_ccaa"
INICIO = datetime(2026, 11, 7, 13, 0, tzinfo=timezone.utc)


def _lee(nombre):
    d = (DATOS / nombre).read_bytes()
    return gzip.decompress(d) if nombre.endswith(".gz") else d


class Respuesta:
    def __init__(self, contenido, url="", cabeceras=None):
        self.content, self.url, self.status_code = contenido, url, 200
        self.headers = cabeceras or {}

    @property
    def text(self):
        return self.content.decode("utf-8")

    def json(self):
        return json.loads(self.content)


class Servidor:
    """Sirve `rutas` ({trozo de URL o (trozo, parámetro=valor): bytes}) y apunta lo pedido."""

    def __init__(self, rutas):
        self.rutas, self.pedidos = rutas, []

    def __call__(self, url, params=None, **kw):
        params = params or {}
        self.pedidos.append((url, dict(params)))
        for clave, datos in self.rutas.items():
            trozo, filtro = clave if isinstance(clave, tuple) else (clave, None)
            if trozo in url and (filtro is None or filtro in "&".join(f"{k}={v}" for k, v in params.items())):
                return Respuesta(datos() if callable(datos) else datos, url)
        raise AssertionError(f"petición no prevista: {url} {params}")


def _lote(tmp_path, fuente, job):
    lote = Lote(AlmacenLocal(tmp_path), fuente, job)
    lote.inicio = INICIO
    return lote


def _claves(tmp_path, prefijo):
    return sorted(o.clave for o in AlmacenLocal(tmp_path).listar(prefijo))


# --- Cataluña ---------------------------------------------------------------------------------

ZIP = _lee("mapa_perill_incendi.zip")
XLSX = _lee("taula_muni_perill_avui.xlsx")


def test_perill_rejilla_y_municipis():
    valido, creacio, n, huella = ccaa.perill_rejilla(ZIP)
    assert (valido, creacio, n) == (date(2026, 11, 7), "20261107 09:30h", 1385)
    valido, n, _ = ccaa.perill_municipis(XLSX, "AVUI")
    assert (valido, n) == (date(2026, 11, 7), 947)
    with pytest.raises(ccaa.Formato, match="columnas"):
        ccaa.perill_municipis(XLSX, "DEMA")
    with pytest.raises(ccaa.Formato, match="no es un ZIP"):
        ccaa.perill_rejilla(b"<html>error</html>")


def _rehace_zip(datos, cambio):
    """El mismo ZIP con `cambio(nombre, bytes)` aplicado a cada miembro."""
    z = zipfile.ZipFile(io.BytesIO(datos))
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as nz:
        for n in z.namelist():
            nz.writestr(n, cambio(n, z.read(n)))
    return b.getvalue()


def test_dbf_con_otro_campo_da_error_claro():
    roto = _rehace_zip(ZIP, lambda n, d: d.replace(b"Creacio", b"Creat\0\0") if n.endswith(".dbf") else d)
    with pytest.raises(ccaa.Formato, match="Creacio"):
        ccaa.perill_rejilla(roto)


def test_cat_perill_guarda_y_no_repite(monkeypatch, tmp_path):
    dema = _rehace_zip(XLSX, lambda n, d: d.replace(b"_AVUI", b"_DEMA").replace(b"07/11/26", b"08/11/26")
                       if n == "xl/sharedStrings.xml" else d)
    s = Servidor({"mapa_perill_incendi.zip": ZIP, "mapa_perill_incendi_dema.zip": ZIP,
                  "taula_muni_perill_avui.xlsx": XLSX, "taula_muni_perill_dema.xlsx": dema})
    monkeypatch.setattr(ccaa, "get", s)
    lote = _lote(tmp_path, "gencat", "cat-perill")
    assert ccaa.cat_perill(lote) == 4
    lote.cerrar()
    claves = _claves(tmp_path, "gencat/")
    assert "gencat/perill/2026/11/07/perill_rejilla_avui_20261107_202611070930.zip" in claves
    assert "gencat/perill/2026/11/07/perill_municipis_dema_20261108_130000.xlsx" in claves
    meta = AlmacenLocal(tmp_path).metadatos(claves[0])
    assert "Llei 37/2007" in meta["licencia"] and meta["origen"].startswith(ccaa.PERILL)

    lote = _lote(tmp_path, "gencat", "cat-perill")
    lote.inicio = INICIO.replace(hour=14)
    assert ccaa.cat_perill(lote) == 0
    assert lote.cerrar() is None


def test_cat_perill_un_fichero_roto_no_para_a_los_demas(monkeypatch, tmp_path):
    s = Servidor({"mapa_perill_incendi_dema.zip": b"<html>404</html>", "mapa_perill_incendi.zip": ZIP,
                  "taula_muni_perill_avui.xlsx": XLSX, "taula_muni_perill_dema.xlsx": XLSX})
    monkeypatch.setattr(ccaa, "get", s)
    lote = _lote(tmp_path, "gencat", "cat-perill")
    with pytest.raises(RuntimeError, match=r"2 fallo\(s\) \(2 guardados\).*rejilla_dema: no es un ZIP"):
        ccaa.cat_perill(lote)
    claves = _claves(tmp_path, "gencat/")
    assert any("fallo_rejilla_dema_" in c for c in claves)
    assert any("perill_rejilla_avui_" in c for c in claves)


def test_cat_alfa(monkeypatch, tmp_path):
    capas = {n: (ruta, campo, 10) for n, (ruta, campo, _) in ccaa.ALFA_CAPAS.items()}
    monkeypatch.setattr(ccaa, "ALFA_CAPAS", capas)
    muni, com = _lee("alfa_municipal_avui_20.json.gz"), _lee("alfa_comarcal_avui.json.gz")
    s = Servidor({"Municipal_Avui_FL_alternatiu_VW/FeatureServer/0/query": muni,
                  "municipal_dema_FL_VW/FeatureServer/5/query": muni,
                  "Comarcal_Avui_FL_VW/FeatureServer/1/query": com,
                  "Comarcal_Dema_FL_VW/FeatureServer/4/query": com,
                  "Municipal": _lee("alfa_municipal_avui_info.json.gz"),
                  "municipal": _lee("alfa_municipal_avui_info.json.gz"),
                  "Comarcal": _lee("alfa_comarcal_avui_info.json.gz")})
    monkeypatch.setattr(ccaa, "get", s)
    lote = _lote(tmp_path, "gencat", "cat-alfa")
    assert ccaa.cat_alfa(lote) == 4
    assert "gencat/pla_alfa/2026/11/07/alfa_municipal_avui_202611070833_130000.json.gz" in _claves(tmp_path, "gencat/")
    assert ccaa.cat_alfa(_lote(tmp_path, "gencat", "cat-alfa")) == 0


# --- Andalucía --------------------------------------------------------------------------------

CAPS = _lee("rediam_capabilities.xml.gz")
TIFF = b"II*\x00" + bytes(200)


def test_rediam_capas():
    capas = ccaa.rediam_capas(CAPS)
    assert [c[:2] for c in capas.values()] == [(date(2026, 10, d), date(2026, 10, 21)) for d in (22, 23, 24)]
    assert capas[1][2] == (85761.5, 3978050.0, 627033.0, 4298700.0)
    with pytest.raises(ccaa.Formato, match="indice_riesgo_dia_2"):
        ccaa.rediam_capas(CAPS.replace(b"indice_riesgo_dia_2", b"otra_capa"))


def test_and_riesgo_baja_una_vez_por_prediccion(monkeypatch, tmp_path):
    s = Servidor({(ccaa.REDIAM, "GetCapabilities"): CAPS, (ccaa.REDIAM, "GetMap"): TIFF})
    monkeypatch.setattr(ccaa, "get", s)
    assert ccaa.and_riesgo(_lote(tmp_path, "rediam", "and-riesgo")) == 3
    claves = _claves(tmp_path, "rediam/")
    assert "rediam/riesgo/2026/10/21/riesgo_pred20261021_d3_20261024.tif.gz" in claves
    assert any(c.startswith("rediam/riesgo/2026/10/21/capabilities_pred20261021_") for c in claves)
    getmap = [p for _, p in s.pedidos if p.get("request") == "GetMap"]
    assert len(getmap) == 3 and getmap[0]["width"] == 2255 and getmap[0]["format"] == "image/tiff"
    # parado: solo GetCapabilities, y nada nuevo aunque el bucket ya no tenga los TIF
    AlmacenLocal(tmp_path).borrar([c for c in claves if c.endswith(".tif.gz")])
    s.pedidos.clear()
    lote = _lote(tmp_path, "rediam", "and-riesgo")
    assert ccaa.and_riesgo(lote) == 0 and lote.cerrar() is None
    assert len(s.pedidos) == 1


def test_and_riesgo_sin_tiff(monkeypatch, tmp_path):
    s = Servidor({(ccaa.REDIAM, "GetCapabilities"): CAPS, (ccaa.REDIAM, "GetMap"): b"<ServiceException/>"})
    monkeypatch.setattr(ccaa, "get", s)
    with pytest.raises(RuntimeError, match="no es un TIFF"):
        ccaa.and_riesgo(_lote(tmp_path, "rediam", "and-riesgo"))
    # sin marcar la predicción: la siguiente pasada lo vuelve a intentar
    assert Lote(AlmacenLocal(tmp_path), "rediam", "x").ultimo("rediam_riesgo") == {}


# --- Navarra --------------------------------------------------------------------------------------

NAV = _lee("navarra_d0.json.gz")


def test_navarra_huella_ignora_id_y_sello():
    fecha, n, h = ccaa.navarra_geojson(NAV, "d0")
    assert (fecha, n) == (date(2026, 11, 7), 289)
    otro = NAV.replace(b"7afe8fe_1a1b1b065b1", b"0000000_0000000000").replace(b"2026-11-07T", b"2026-11-08T")
    assert otro != NAV and ccaa.navarra_geojson(otro, "d0")[2] == h
    assert ccaa.navarra_geojson(NAV.replace(b'"Alerta":"Bajo","Fecha riesgo":"07/11/2026","IdRiesgo":1',
                                            b'"Alerta":"Alto","Fecha riesgo":"07/11/2026","IdRiesgo":3', 1), "d0")[2] != h
    with pytest.raises(ccaa.Formato, match="no es JSON"):
        ccaa.navarra_geojson(b"<ows:ExceptionReport/>", "d0")


def test_nav_riesgo(monkeypatch, tmp_path):
    dias = {d: NAV.replace(b"07/11/2026", f"{7 + d:02d}/11/2026".encode()) for d in range(3)}
    s = Servidor({(ccaa.NAVARRA, f"vw_riesgomunicipiod{d}"): dias[d] for d in range(3)})
    monkeypatch.setattr(ccaa, "get", s)
    assert ccaa.nav_riesgo(_lote(tmp_path, "navarra", "nav-riesgo")) == 3
    assert "navarra/riesgo/2026/11/07/riesgo_d2_20261109_130000.geojson.gz" in _claves(tmp_path, "navarra/")
    assert ccaa.nav_riesgo(_lote(tmp_path, "navarra", "nav-riesgo")) == 0


def test_nav_riesgo_fecha_vieja(monkeypatch, tmp_path):
    s = Servidor({ccaa.NAVARRA: NAV.replace(b"07/11/2026", b"01/11/2026")})
    monkeypatch.setattr(ccaa, "get", s)
    with pytest.raises(RuntimeError, match="demasiado lejos"):
        ccaa.nav_riesgo(_lote(tmp_path, "navarra", "nav-riesgo"))


# --- Castilla-La Mancha ---------------------------------------------------------------------------

def test_clm_ipp(monkeypatch, tmp_path):
    pdf = b"%PDF-1.7 falso"
    s = Servidor({f"{ccaa.INFOCAM}/query": _lee("infocam_ipp.json.gz"), ccaa.INFOCAM: _lee("infocam_info.json.gz"),
                  ccaa.INFOCAM_PAGINA: _lee("infocam_pagina.html.gz"), "IPP_20261104_09.pdf": pdf})
    monkeypatch.setattr(ccaa, "get", s)
    assert ccaa.clm_ipp(_lote(tmp_path, "infocam", "clm-ipp")) == 2
    claves = _claves(tmp_path, "infocam/")
    assert claves == ["infocam/ipp/2026/11/07/ipp_20261104_20261109_130000.json.gz",
                      "infocam/ipp/2026/11/07/ipp_pdf_130000_IPP_20261104_09.pdf"]
    assert ccaa.clm_ipp(_lote(tmp_path, "infocam", "clm-ipp")) == 0


def test_clm_nivel_desconocido():
    q = json.loads(_lee("infocam_ipp.json.gz"))
    q["features"][0]["attributes"]["IPPadmin"] = "Rojo"
    with pytest.raises(ccaa.Formato, match="rojo"):
        ccaa.infocam_ipp(q)


# --- Comunitat Valenciana -------------------------------------------------------------------------

VAL = _lee("vaersa_lista.html.gz")


def test_vaersa_tabla():
    _, dias = ccaa.vaersa_tabla(VAL.decode())
    assert sorted(dias) == [date(2026, 11, d) for d in (5, 6, 7)]
    assert dias[date(2026, 11, 7)]["Alerta"] == ["1"] * 7 and "Tormenta" in dias[date(2026, 11, 5)]
    with pytest.raises(ccaa.Formato, match="cabecera"):
        ccaa.vaersa_tabla(VAL.decode().replace("Z. 1N", "Zona 1N"))


def test_val_preemergencia(monkeypatch, tmp_path):
    s = Servidor({ccaa.VAERSA: VAL})
    monkeypatch.setattr(ccaa, "get", s)
    lote = _lote(tmp_path, "vaersa", "val-preemergencia")
    lote.inicio = datetime(2026, 11, 6, 13, 0, tzinfo=timezone.utc)      # la muestra trae del 05 al 07
    assert ccaa.val_preemergencia(lote) == 1
    assert s.pedidos[0][1]["desde"] == "05/11/2026" and s.pedidos[0][1]["hasta"] == "08/11/2026"
    meta = AlmacenLocal(tmp_path).metadatos(_claves(tmp_path, "vaersa/")[0])
    assert "NO comercial" in meta["licencia"]
    # la misma tabla con otro HTML alrededor no se vuelve a guardar
    s.rutas[ccaa.VAERSA] = VAL.replace(b"</body>", b"<!-- otra -->\n</body>")
    assert ccaa.val_preemergencia(lote) == 0


# --- La Rioja -------------------------------------------------------------------------------------

def _rioja(ref_pagina="06/11/2026"):
    pagina = _lee("rioja_pagina.html.gz").replace(b'value="06/11/2026"', f'value="{ref_pagina}"'.encode())
    js = {f"{7 + k - 1}/11/2026": _lee(f"rioja_ref20261106_d{k}_{20261106 + k}.js.gz") for k in range(1, 8)}
    return pagina, js


def test_rioja_datos():
    _, js = _rioja()
    zonas = ccaa.rioja_datos(js["7/11/2026"].decode(), date(2026, 11, 7))
    assert zonas[0] == ("OBARENES-TOLOÑO", "Moderado", 12.29) and len(zonas) == 5
    assert ccaa.rioja_datos(_lee("rioja_vacia.js.gz").decode(), date(2026, 11, 7)) == []
    with pytest.raises(ccaa.Formato, match="fecha"):
        ccaa.rioja_datos(js["7/11/2026"].decode(), date(2026, 11, 8))


def test_rio_ipogif(monkeypatch, tmp_path):
    pagina, js = _rioja()
    monkeypatch.setattr(ccaa, "get", Servidor({ccaa.RIOJA: pagina}))
    monkeypatch.setattr(ccaa.time, "sleep", lambda s: None)
    pedidos = []

    def post(url, datos):
        pedidos.append(datos)
        if datos["fecha_referencia"] == "06/11/2026":
            return Respuesta(js[datos["fecha_datos"]], url)
        return Respuesta(_lee("rioja_vacia.js.gz"), url)        # la del 05 no se ofrece en la muestra
    monkeypatch.setattr(ccaa, "_post", post)
    assert ccaa.rio_ipogif(_lote(tmp_path, "larioja", "rio-ipogif")) == 1
    clave = "larioja/ipogif/2026/11/06/ipogif_ref20261106.tar.gz"
    assert _claves(tmp_path, "larioja/") == [clave]
    with tarfile.open(fileobj=io.BytesIO(AlmacenLocal(tmp_path).leer(clave))) as t:
        assert len(t.getnames()) == 8 and t.extractfile("d7_20261113.js").read() == js["13/11/2026"]
    # ya guardada: ni un POST
    pedidos.clear()
    assert ccaa.rio_ipogif(_lote(tmp_path, "larioja", "rio-ipogif")) == 0 and pedidos == []


def test_rio_ipogif_ultima_sin_datos(monkeypatch, tmp_path):
    pagina, _ = _rioja()
    monkeypatch.setattr(ccaa, "get", Servidor({ccaa.RIOJA: pagina}))
    monkeypatch.setattr(ccaa, "_post", lambda url, datos: Respuesta(_lee("rioja_vacia.js.gz"), url))
    with pytest.raises(RuntimeError, match="no trae datos"):
        ccaa.rio_ipogif(_lote(tmp_path, "larioja", "rio-ipogif"))


# --- vigilancia -----------------------------------------------------------------------------------

def test_limites_de_campana():
    assert ccaa.limite_and(date(2026, 7, 1)) == 36 and ccaa.limite_and(date(2026, 11, 7)) is None
    assert ccaa.limite_clm(date(2026, 8, 1)) == 36 and ccaa.limite_clm(date(2026, 1, 10)) == 192


def test_estado_fuera_de_campana_solo_mira_el_latido(monkeypatch, tmp_path, capsys):
    almacen = AlmacenLocal(tmp_path)
    monkeypatch.setattr(principal.alm, "abrir", lambda: almacen)
    monkeypatch.setattr(principal, "_jobs", lambda: {"and-riesgo": ("rediam", None, None)})
    assert principal.estado() == 1                       # sin latido
    almacen.guardar("estado/latido_and-riesgo.json",
                    json.dumps({"fin": datetime.now(timezone.utc).isoformat(), "nuevos": 0}).encode(), {})
    assert principal.estado() == 0                       # sin datos nuevos desde hace semanas, pero termina bien
    assert "fuera de campaña" in capsys.readouterr().out
