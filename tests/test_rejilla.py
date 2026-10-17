import numpy as np
import pytest
import xarray as xr

from fuego_datos import rejilla
from fuego_datos.fuentes import aemet, ecmwf

ec = pytest.importorskip("eccodes")


def _grib_global(lon0=0.0, valor=lambda la, lo: la * 1000 + lo):
    """Mensaje GRIB2 global de 1° con valores que codifican su posición."""
    gid = ec.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    ni, nj = 360, 181
    ec.codes_set(gid, "Ni", ni)
    ec.codes_set(gid, "Nj", nj)
    ec.codes_set(gid, "latitudeOfFirstGridPointInDegrees", 90.0)
    ec.codes_set(gid, "latitudeOfLastGridPointInDegrees", -90.0)
    ec.codes_set(gid, "longitudeOfFirstGridPointInDegrees", lon0)
    ec.codes_set(gid, "longitudeOfLastGridPointInDegrees", lon0 + 359.0)
    ec.codes_set(gid, "iDirectionIncrementInDegrees", 1.0)
    ec.codes_set(gid, "jDirectionIncrementInDegrees", 1.0)
    ec.codes_set(gid, "shortName", "2t")
    ec.codes_set(gid, "dataDate", 20261017)
    ec.codes_set(gid, "dataTime", 0)
    ec.codes_set(gid, "endStep", 6)
    ec.codes_set(gid, "bitsPerValue", 24)
    lats = 90.0 - np.arange(nj)
    lons = (lon0 + np.arange(ni) + 180) % 360 - 180
    ec.codes_set_values(gid, valor(lats[:, None], lons[None, :]).ravel())
    msg = ec.codes_get_message(gid)
    ec.codes_release(gid)
    return msg


@pytest.mark.parametrize("lon0", [0.0, -180.0])
def test_recorte_cruza_el_meridiano_cero(lon0):
    c = rejilla.leer_campo(_grib_global(lon0))
    assert c["lats"][0] == 44 and c["lats"][-1] == 36            # de norte a sur, 44.2 → 35.6
    assert c["lons"][0] == -10 and c["lons"][-1] == 4            # -10.0 → 4.6, ordenadas
    assert np.all(np.diff(c["lons"]) == 1)
    esperado = c["lats"][:, None] * 1000 + c["lons"][None, :]
    np.testing.assert_allclose(c["valores"], esperado, atol=0.01)
    assert c["paso"] == 6 and c["validez"].hour == 6


def test_separar_y_a_netcdf(tmp_path):
    bloque = _grib_global() + _grib_global()
    msgs = list(rejilla.separar_mensajes(bloque))
    assert len(msgs) == 2
    campos = [rejilla.leer_campo(m) for m in msgs[:1]]
    ruta = tmp_path / "x.nc"
    ruta.write_bytes(rejilla.a_netcdf(campos, {"fuente": "prueba"}))
    ds = xr.open_dataset(ruta)
    assert "2t" in ds and ds["2t"].shape == (1, 9, 15)
    assert ds.attrs["fuente"] == "prueba"


def test_precision():
    assert rejilla.precision("t_2m") == 1
    assert rejilla.precision("vsw_3") == 3
    assert rejilla.precision("w_so_9") == 1
    assert rejilla.precision("desconocida") == 2


def test_rangos_ecmwf_une_contiguos():
    lineas = [
        {"param": "2t", "levtype": "sfc", "_offset": 0, "_length": 10},
        {"param": "2d", "levtype": "sfc", "_offset": 10, "_length": 5},
        {"param": "ttr", "levtype": "sfc", "_offset": 15, "_length": 7},     # no se quiere
        {"param": "10u", "levtype": "sfc", "_offset": 22, "_length": 3},
        {"param": "t", "levtype": "pl", "levelist": "850", "_offset": 25, "_length": 4},
        {"param": "t", "levtype": "pl", "levelist": "500", "_offset": 29, "_length": 4},  # nivel no
    ]
    campos = ecmwf.elegidos(lineas)
    assert campos == [(0, 10), (10, 5), (22, 3), (25, 4)]
    assert ecmwf.rangos(campos, hueco=0) == [(0, 15), (22, 7)]
    assert ecmwf.rangos(campos, hueco=7) == [(0, 29)]          # se salta ttr


def test_nombre_gzip():
    import gzip
    import io
    buf = io.BytesIO()
    with gzip.GzipFile(filename="1792242605_municipios_h_json.tar", mode="wb", fileobj=buf) as g:
        g.write(b"x")
    assert aemet.nombre_gzip(buf.getvalue()) == "1792242605_municipios_h_json.tar"
