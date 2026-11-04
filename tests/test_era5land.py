from datetime import datetime, timedelta, timezone

import netCDF4
import numpy as np
import pytest

from fuego_datos.almacen import AlmacenLocal, Lote
from fuego_datos.fuentes import era5land


def nc(n_horas):
    ds = netCDF4.Dataset("x", "w", memory=1024)
    ds.createDimension("valid_time", n_horas)
    v = ds.createVariable("t2m", "f4", ("valid_time",))
    v[:] = np.zeros(n_horas)
    return bytes(ds.close())


def dia(d):
    hoy = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return hoy - timedelta(days=d)


@pytest.fixture
def lote(tmp_path):
    return Lote(AlmacenLocal(tmp_path), "era5land", "era5land")


def guardado(lote, d):
    c = list(lote.almacen.listar(lote.clave("horario", f"era5land_{dia(d):%Y%m%d}.", dia(d))))
    return era5land.horas(lote.almacen.leer(c[0].clave)) if c else None


def test_horas_cuenta_valid_time():
    assert era5land.horas(nc(17)) == 17


def test_guarda_incompletos_y_los_sustituye(lote, monkeypatch):
    # Pasada 1: los días viejos completos, D-7 a medias, D-6 casi vacío, D-5 aún no está
    def cds(d):
        edad = (dia(0) - d).days
        if edad == 5:
            raise RuntimeError("no disponible")
        return nc({7: 15, 6: 2}.get(edad, 24))
    monkeypatch.setattr(era5land, "bajar_dia", cds)
    era5land.ejecutar(lote)
    assert guardado(lote, 8) == 24 and guardado(lote, 7) == 15 and guardado(lote, 6) == 2
    assert guardado(lote, 5) is None

    # Pasada 2: D-7 se completa, D-6 no cambia; los completos no se vuelven a pedir
    pedidos = []
    def cds2(d):
        pedidos.append((dia(0) - d).days)
        edad = pedidos[-1]
        if edad == 5:
            raise RuntimeError("no disponible")
        return nc({6: 2}.get(edad, 24))
    monkeypatch.setattr(era5land, "bajar_dia", cds2)
    era5land.ejecutar(lote)
    assert pedidos == [7, 6, 5]
    assert guardado(lote, 7) == 24 and guardado(lote, 6) == 2


def test_fichero_sin_horas_falla(lote, monkeypatch):
    monkeypatch.setattr(era5land, "bajar_dia", lambda d: nc(0))
    with pytest.raises(RuntimeError, match="sin horas"):
        era5land.ejecutar(lote)
