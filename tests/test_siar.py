"""SiAR con una API falsa: presupuesto, orden (lo más reciente primero) y que
una segunda pasada no repite nada."""
import gzip
import json
from datetime import date, timedelta

import pytest

from fuego_datos.almacen import AlmacenLocal, Lote
from fuego_datos.fuentes import siar


def test_grados():
    assert siar._grados("391520000N") == pytest.approx(39 + 15 / 60 + 20 / 3600)
    assert siar._grados("015512000W") == pytest.approx(-(1 + 55 / 60 + 12 / 3600))


class ApiFalsa:
    """5 estaciones en el dominio y 1 en Canarias. Cuenta registros y peticiones."""

    def __init__(self, cuota=10_000):
        self.cuota, self.registros, self.pedidos = cuota, 0, []
        self.minima = date.today() - timedelta(days=siar.DIAS_VENTANA)

    def __call__(self, ruta, token, **p):
        if ruta == "Info/ACCESOS":
            return [{"MaxRegistrosDia": self.cuota, "RegistrosAcumuladosDia": self.registros,
                     "MaxRegistrosMinuto": 100}]
        if ruta == "Info/ESTACIONES":
            est = [{"Codigo": f"P{i}", "Estacion": f"e{i}", "IdProvincia": i % 2,
                    "Latitud": "400000000N", "Longitud": "030000000W", "Fecha_Baja": None}
                   for i in range(5)]
            est.append({"Codigo": "TF1", "Estacion": "canarias", "IdProvincia": 38,
                        "Latitud": "281000000N", "Longitud": "163000000W", "Fecha_Baja": None})
            self.registros += len(est)
            return est
        tipo = ruta.split("/")[1]
        d = date.fromisoformat(p["FechaInicial"])
        if d < self.minima:
            return None
        n = 1 if tipo == "Diarios" else siar.SEMIHORAS
        filas = [{"Fecha": f"{d}T00:00:00", "Estacion": c, "HorMin": k} for c in p["Id"] for k in range(n)]
        assert len(filas) <= 100, "una petición no puede pasar de 100 registros"
        assert len(p["Id"]) <= siar.POR_PETICION, "demasiadas estaciones en una petición"
        self.registros += len(filas)
        self.pedidos += [(tipo, str(d), c) for c in p["Id"]]
        return filas


@pytest.fixture
def entorno(monkeypatch, tmp_path):
    api = ApiFalsa()
    monkeypatch.setattr(siar, "_pide", api)
    monkeypatch.setattr(siar.time, "sleep", lambda s: None)
    monkeypatch.setenv("SIAR_TOKEN", "x")
    monkeypatch.setenv("SIAR_REGISTROS_DIA", "800")
    monkeypatch.setenv("SIAR_REGISTROS_DIARIOS", "20")
    return api, AlmacenLocal(tmp_path)


def pasada(almacen):
    lote = Lote(almacen, "siar", "siar")
    n = siar.ejecutar(lote)
    lote.cerrar()
    return n


def test_reciente_primero_sin_canarias_y_sin_repetir(entorno):
    api, almacen = entorno
    pasada(almacen)
    ayer = str(date.today() - timedelta(days=1))
    diarios = [p for p in api.pedidos if p[0] == "Diarios"]
    horarios = [p for p in api.pedidos if p[0] == "Horarios"]
    assert all(c != "TF1" for _, _, c in api.pedidos)
    # 20 registros de diarios: 5 estaciones × 4 días, empezando por ayer
    assert len(diarios) == 20 and diarios[0][1] == ayer
    assert sorted({d for _, d, _ in diarios}, reverse=True)[0] == ayer
    # semihorarios: lo que cabe en el resto, de ayer hacia atrás
    assert horarios and horarios[0][1] == ayer
    primera = list(api.pedidos)

    pasada(almacen)                                 # al día siguiente (mismo día aquí)
    segunda = api.pedidos[len(primera):]
    assert not set(segunda) & set(primera), "se ha bajado dos veces el mismo dato"
    hecho = json.loads(gzip.decompress(almacen.leer(siar.ESTADO_HECHO)))
    assert set(hecho["Diarios"][ayer]) == {f"P{i}" for i in range(5)}


def test_no_pasa_de_la_ventana(entorno, monkeypatch):
    api, almacen = entorno
    monkeypatch.setenv("SIAR_REGISTROS_DIARIOS", "5000")
    monkeypatch.setenv("SIAR_REGISTROS_DIA", "5000")
    api.cuota = 100_000
    pasada(almacen)
    fechas = {d for t, d, _ in api.pedidos if t == "Diarios"}
    assert min(fechas) == str(api.minima)
    assert len(fechas) == siar.DIAS_VENTANA
