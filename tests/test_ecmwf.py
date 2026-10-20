from datetime import date, datetime, timezone

from fuego_datos.fuentes.ecmwf import _url, flujo


def test_flujo_scda_hasta_mayo_2026():
    assert flujo(date(2025, 7, 15), 18) == "scda"
    assert flujo(date(2026, 5, 11), 6) == "scda"
    assert flujo(date(2026, 5, 12), 18) == "oper"
    assert flujo(date(2025, 7, 15), 0) == "oper"
    assert flujo(datetime(2024, 6, 1, tzinfo=timezone.utc), 12) == "oper"


def test_url_con_su_carpeta():
    u = _url(datetime(2025, 7, 15, tzinfo=timezone.utc), 18, "0p25", 54, "index")
    assert u.endswith("/20250715/18z/ifs/0p25/scda/20250715180000-54h-scda-fc.index")


def test_recorrer_vuelve_a_por_las_fallidas(monkeypatch):
    from fuego_datos.fuentes import ecmwf
    intentos = {}

    def una(lote, fecha, hora, resol):
        intentos[hora] = intentos.get(hora, 0) + 1
        if hora == 6 and intentos[hora] < 3:
            raise IOError("503 Slow Down")
        return True

    monkeypatch.setattr(ecmwf, "resoluciones", lambda f, h: ["0p25"])
    monkeypatch.setattr(ecmwf, "_una", una)
    monkeypatch.setattr(ecmwf.time, "sleep", lambda s: None)
    dia = datetime(2025, 6, 8, tzinfo=timezone.utc)
    pasadas = [(dia, h) for h in (0, 6, 12)]

    assert ecmwf._recorrer(None, pasadas, lambda r: r, esperas=(1, 1)) == 3
    assert intentos == {0: 1, 6: 3, 12: 1}

    intentos.clear()
    try:
        ecmwf._recorrer(None, pasadas, lambda r: r, esperas=(1,))
    except RuntimeError as e:
        assert "2025-06-08 06z 0p25" in str(e) and "2 pasada(s)" in str(e)
    else:
        raise AssertionError("debía fallar")
