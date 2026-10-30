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


def test_pasos_cada_6_h_despues_de_144():
    from fuego_datos.fuentes.ecmwf import pasos
    assert pasos(144) == list(range(0, 145, 3))
    assert pasos(90) == list(range(0, 91, 3))
    p = pasos(360)
    assert len(p) == 85 and p[48:51] == [144, 150, 156] and p[-1] == 360


def test_ultimo_paso_de_las_largas(monkeypatch):
    from fuego_datos.fuentes import ecmwf
    hay = set()
    monkeypatch.setattr(ecmwf, "_existe", lambda f, h, r, p: p in hay)
    dia = datetime(2026, 10, 24, tzinfo=timezone.utc)
    pronto = datetime(2026, 10, 24, 8, tzinfo=timezone.utc)
    tarde = datetime(2026, 10, 24, 13, tzinfo=timezone.utc)

    hay |= {144}
    assert ecmwf.ultimo_paso(dia, 0, "0p25", pronto) is None      # aún esperando las 360 h
    assert ecmwf.ultimo_paso(dia, 0, "0p25", tarde) == 144        # no llega: lo que haya
    assert ecmwf.ultimo_paso(dia, 6, "0p25", pronto) == 144       # las de 06/18, cortas
    assert ecmwf.ultimo_paso(dia, 0, "0p1", pronto) == 144        # la malla fina, corta
    hay |= {360}
    assert ecmwf.ultimo_paso(dia, 12, "0p25", pronto) == 360
    assert ecmwf.paso_largo(datetime(2024, 11, 12), 0, "0p25") == 240
    assert ecmwf.paso_largo(datetime(2024, 11, 12), 12, "0p25") == 360


def test_url_de_una_parte():
    u = _url(datetime(2026, 10, 24, tzinfo=timezone.utc), 0, "0p1", 24, "index", "sfc")
    assert u.endswith("/20261024/00z/ifs/0p1/oper/20261024000000-24h-oper-fc-sfc.index")


def test_partes_una_o_dos(monkeypatch):
    from fuego_datos.fuentes import ecmwf

    class R:
        def __init__(self, text):
            self.text = text
    listados = {
        "0p25": "<Key>a/20261024000000-0h-oper-fc.grib2</Key><Key>a/20261024000000-0h-oper-fc.index</Key>",
        "0p1": "<Key>a/20261024000000-0h-oper-fc-pl.index</Key><Key>a/20261024000000-0h-oper-fc-sfc.index</Key>",
        "0p05": "",
    }
    monkeypatch.setattr(ecmwf, "_PARTES", {})
    monkeypatch.setattr(ecmwf, "get", lambda url, params: R(listados[params["prefix"].split("/")[3]]))
    dia = datetime(2026, 10, 24, tzinfo=timezone.utc)
    assert ecmwf.partes(dia, 0, "0p25") == (None,)
    assert ecmwf.partes(dia, 0, "0p1") == ("pl", "sfc")
    assert ecmwf.partes(dia, 0, "0p05") == ()


def test_listados_del_portal(monkeypatch):
    from fuego_datos.fuentes import ecmwf

    class R:
        def __init__(self, text):
            self.text = text
    paginas = {
        "20261030/00z/ifs/": '<a href="/forecasts/20261030/00z/ifs/0p25/">0p25</a>'
                             '<a href="/forecasts/20261030/00z/ifs/0p1/">0p1</a>',
        "20261030/00z/ifs/0p25/oper/": '<a href="/forecasts/20261030/00z/ifs/0p25/oper/20261030000000-0h-oper-fc.grib2">'
                                       '<a href="/forecasts/20261030/00z/ifs/0p25/oper/20261030000000-0h-oper-fc.index">',
        "20261030/00z/ifs/0p1/oper/": '<a href="/forecasts/20261030/00z/ifs/0p1/oper/20261030000000-0h-oper-fc-pl.index">'
                                      '<a href="/forecasts/20261030/00z/ifs/0p1/oper/20261030000000-0h-oper-fc-sfc.index">',
    }
    monkeypatch.setattr(ecmwf, "BASE", ecmwf.PORTAL)
    monkeypatch.setattr(ecmwf, "_PARTES", {})
    monkeypatch.setattr(ecmwf, "get", lambda url: R(paginas[url.removeprefix(ecmwf.PORTAL + "/")]))
    dia = datetime(2026, 10, 30, tzinfo=timezone.utc)
    assert ecmwf.resoluciones(dia, 0) == ["0p1", "0p25"]
    assert ecmwf.partes(dia, 0, "0p25") == (None,)
    assert ecmwf.partes(dia, 0, "0p1") == ("pl", "sfc")
    assert _url(dia, 0, "0p25", 3, "index").startswith(ecmwf.PORTAL + "/20261030/00z/")


def test_lo_reciente_primero_del_portal(monkeypatch):
    from datetime import timedelta
    from fuego_datos.fuentes import ecmwf
    usadas, guardadas = [], set()
    portal_va = {"ok": True}

    def una(lote, fecha, hora, resol):
        usadas.append((fecha.day, hora, resol, ecmwf.BASE))
        if (fecha, hora, resol) in guardadas:
            return False
        if ecmwf.BASE == ecmwf.PORTAL and not portal_va["ok"]:
            raise IOError("portal caído")
        guardadas.add((fecha, hora, resol))
        return True

    # el portal solo tiene 0,25°; AWS también 0,1°
    monkeypatch.setattr(ecmwf, "resoluciones",
                        lambda f, h: ["0p25"] if ecmwf.BASE == ecmwf.PORTAL else ["0p1", "0p25"])
    monkeypatch.setattr(ecmwf, "_una", una)
    hoy = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    viejo = hoy - timedelta(days=30)
    A, P = ecmwf.AWS, ecmwf.PORTAL

    hechas, fallidas = ecmwf._vuelta(None, [(viejo, 0), (hoy, 0)], lambda r: r)
    assert usadas == [(viejo.day, 0, "0p1", A), (viejo.day, 0, "0p25", A),     # lo viejo, solo AWS
                      (hoy.day, 0, "0p25", P),                                   # lo reciente, portal
                      (hoy.day, 0, "0p1", A), (hoy.day, 0, "0p25", A)]          # y lo que falte, AWS
    assert hechas == 4 and fallidas == [] and ecmwf.BASE == A

    usadas.clear(); guardadas.clear(); portal_va["ok"] = False
    hechas, fallidas = ecmwf._vuelta(None, [(hoy, 6)], lambda r: r)
    assert hechas == 2 and fallidas == []                                        # el portal falla: todo de AWS
