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
