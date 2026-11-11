# Índices de peligro de incendio de las comunidades autónomas

Siete jobs en `fuego_datos/fuentes/ccaa.py` que guardan cada día el índice oficial
de peligro de Cataluña, Andalucía, Navarra, Castilla-La Mancha, la Comunitat
Valenciana y La Rioja, para compararlo con VIGÍA. Galicia va aparte
(`docs/xunta_irdi.md`). El inventario de fuentes y su histórico está en
`r10-mejoras/indices_ccaa/INFORME_INDICES_CCAA.md` (fuera de este repo).

Salvo Valencia y La Rioja, que dejan pedir fechas pasadas, **no hay archivo**:
lo que no se baja ese día se pierde.

## Jobs

| Job | Workflow (UTC) | Qué se pide | Qué se guarda (en `<fuente>/<producto>/AAAA/MM/DD/`) | Al día |
|---|---|---|---|---|
| `cat-perill` | `ccaa_cataluna.yml`, 09:20, 12:20, 15:20 y 23:20 | `gencat.cat/medinatural/incendis/mapes/`: `mapa_perill_incendi.zip` y `_dema.zip` (rejilla de 5 km, 1.385 celdas, SHP en EPSG:25831), `taula_muni_perill_avui.xlsx` y `_dema.xlsx` (947 municipios) | `gencat/perill/perill_<rejilla\|municipis>_<avui\|dema>_<AAAAMMDD válido>_<creació o hhmmss>.<zip\|xlsx>` | 0,2-0,4 MB |
| `cat-alfa` | el mismo | Pla Alfa en ArcGIS Online (`services7.arcgis.com/ZCqVt1fRXwwK6GF4`): municipal y comarcal, hoy y mañana, sin geometría | `gencat/pla_alfa/alfa_<capa>_<lastEditDate>_<hhmmss>.json.gz` | 0,1-0,3 MB |
| `and-riesgo` | `ccaa_andalucia.yml`, 07:40, 11:40, 15:40 y 19:40 | WMS `REDIAM_Indice_Riesgo_Incendios_Diario`: GetCapabilities y, si cambia la predicción, GetMap en GeoTIFF de `indice_riesgo_dia_1..3` (EPSG:25830, 240 m, 2255×1336) | `rediam/riesgo/<AAAA/MM/DD de la predicción>/riesgo_pred<AAAAMMDD>_d<k>_<válido>.tif.gz` y `capabilities_pred<AAAAMMDD>_<hhmmss>.xml.gz` | 2,2 MB en campaña; 0 fuera |
| `nav-riesgo` | `ccaa_navarra.yml`, 06:50, 10:50, 14:50 y 18:50 | WFS de IDENA, `riesgoIncendios:vw_riesgomunicipiod0..2`, GeoJSON con geometría (289 municipios y facerías) | `navarra/riesgo/riesgo_d<k>_<válido>_<hhmmss>.geojson.gz` | 1,7 MB (3,4 si cambia dos veces) |
| `clm-ipp` | `ccaa_clm.yml`, 08:35, 15:35 y 19:35 | ArcGIS `IPP_Administrativo_Web_Vista_2025` (921 municipios con código INE, sin geometría) y el PDF del último parte, enlazado en `infocam.castillalamancha.es/indice-de-propagacion-potencial` | `infocam/ipp/ipp_<primer día>_<último día>_<hhmmss>.json.gz` e `ipp_pdf_<hhmmss>_<nombre original>.pdf` | ~0,5 MB en campaña; ~0,1 fuera |
| `val-preemergencia` | `ccaa_valencia.yml`, 08:05, 13:05, 17:05 y 21:05 | `prevencionincendiosgva.es/Meteorologia/NivelPreemergenciaList` de ayer a pasado mañana (7 zonas; filas «Alerta» y «Tormenta») | `vaersa/preemergencia/preemergencia_<último día>_<hhmmss>.html.gz` | <0,1 MB |
| `rio-ipogif` | `ccaa_rioja.yml`, 07:45, 13:45 y 19:45 | `bi.larioja.org/pentaho/riesgo_incendios/`: la última fecha de referencia del selector y, por cada una, 7 POST a `datos.jsp` (D+1 a D+7, 5 zonas, nivel y valor) | `larioja/ipogif/<AAAA/MM/DD de la referencia>/ipogif_ref<AAAAMMDD>.tar.gz` (las 7 respuestas JS tal cual y `peticiones.json`) | <0,01 MB |

En total, unos 5 MB al día en campaña y 2,5 MB fuera (medido el 07/11/2026). Con
la retención de 3 días, ~15 MB en el bucket.

**No se baja** el ZIP municipal de Cataluña (`mapa_perill_incendi_municipis.zip` y
`_dema.zip`): son ~15 MB cada uno por la geometría detallada, unos 30 MB al día.
El nivel por municipio ya está en el XLSX (código de 5 cifras del INE).

Las horas de publicación medidas el 07/11/2026 están en el comentario de cada
workflow. De Andalucía, Navarra, Valencia y La Rioja no se conoce la hora exacta:
por eso hay varias pasadas, y cada una solo guarda si algo ha cambiado.

## Cómo se evita guardar dos veces lo mismo

Todos usan `Lote.guardar_si_cambia`. Cuando la respuesta cambia en cada petición
sin que cambie el dato, se compara una huella de lo que importa (parámetro
`huella`, nuevo en `almacen.py`):

- Cataluña ZIP: el contenido de cada fichero del ZIP, no el ZIP (lleva fechas de compresión).
- Cataluña XLSX: la hoja y `sharedStrings.xml` (el resto lleva la hora de generación).
- Navarra: propiedades y geometría, sin los `id` ni el `timeStamp`, que cambian en cada petición.
- Valencia: solo la tabla, no la página.
- Andalucía: los títulos de las tres capas («Índice riesgo incendios 22-10-2026
  (predicción 21-10-2026)»). Si son los de lo último guardado (`Lote.ultimo`, en
  `estado/`, que no se borra), no se piden los GeoTIFF.
- La Rioja: se piden solo las referencias posteriores a la última guardada.

El Pla Alfa municipal no lleva fecha: si no cambia de un día a otro (todo a 0 en
invierno), no se vuelve a guardar. El comarcal sí lleva `DATA` y `HORA`, y
`lastEditDate` de la capa va en el nombre y en los metadatos.

## Errores

- Cada job pide sus ficheros por separado: si uno falla, se guardan los demás y
  el job termina con error con todos los motivos. Un job que falla no para a los
  de las otras comunidades (`bajar` sigue con el siguiente).
- Si la forma de la respuesta cambia (no es ZIP/XLSX/JSON/TIFF, faltan campos o
  columnas, número de celdas o municipios distinto, niveles desconocidos, fechas
  a más de unos días de hoy), se guarda la respuesta en `<producto>/…/fallo_<qué>_<hhmmss>.gz`
  y el job falla con el motivo.
- Comprobaciones concretas: 1.385 celdas de perill (niveles 0-5) y 947 municipios
  (baix, moderat, alt, molt alt, extrem); Pla Alfa con al menos 900 municipios o
  40 comarcas y nivel entero 0-5; las tres capas de REDIAM de la misma predicción;
  Navarra con al menos 280 recintos, una sola fecha e `IdRiesgo` 0-5; CLM con al
  menos 900 municipios, un solo periodo y niveles bajo…extremo; Valencia con las
  zonas Z. 1N a Z. 6 y niveles 1-3; La Rioja con 5 zonas y la fecha pedida.
  Si en verano sale un nivel que aún no se ha visto con otro nombre, el job
  fallará y habrá que añadirlo.

## Vigilancia (`estado`)

Límite de horas sin datos nuevos, en `_jobs()`:

| Job | Límite | Por qué |
|---|---|---|
| `cat-perill`, `cat-alfa`, `nav-riesgo`, `val-preemergencia` | 30 h | publican todos los días del año y el fichero lleva la fecha |
| `rio-ipogif` | 36 h; **pausado** desde el 10/11/2026 | una referencia al día, a hora sin medir |
| `clm-ipp` | 36 h del 1/06 al 15/10; fuera, 192 h | fuera de campaña hay un parte por semana; 192 h caben en los 9 días que el bucket guarda los manifiestos |
| `and-riesgo` | 36 h del 1/06 al 15/10; fuera, **ninguno** | el WMS deja de actualizarse fuera de campaña (el 07/11/2026 seguía en la predicción del 21/10) |

**La Rioja, pausada (10/11/2026).** `bi.larioja.org` no ha respondido a GitHub
ninguna vez desde el alta del 07/11 (522; desde el portátil, 403). El cron de
`ccaa_rioja.yml` está comentado y el job está en `PAUSADOS` de
`fuego_datos/__main__.py`, así que `estado` no lo vigila; se puede lanzar a mano.
El acceso se pide en `r10-mejoras/indices_ccaa/SOLICITUDES.md`.

Con límite «ninguno» (`None`), `estado` solo exige que el job haya terminado bien
en las últimas 30 h (latido): avisa si el servicio se cae o cambia de formato,
pero no por no publicar. Si en primavera REDIAM empieza a publicar antes del 1/06,
se guarda igual; si el 1/06 sigue parado, `estado` avisará a las 36 h.

## Licencias

| Fuente | Licencia | Uso comercial | Cita |
|---|---|---|---|
| Cataluña, perill | Avís legal de gencat.cat: Llei 37/2007, Llicència oberta d'ús d'informació – Catalunya (comprobado el 07/11/2026) | Sí | «Generalitat de Catalunya – Servei de Prevenció d'Incendis Forestals», con la fecha de actualización |
| Cataluña, Pla Alfa | ArcGIS Online sin licencia propia; se supone el avís legal de gencat.cat | Sin confirmar | «Generalitat de Catalunya – Cos d'Agents Rurals» |
| Andalucía, REDIAM | WMS: uso libre y gratuito citando autores; condiciones del portal: reutilización comercial citando fuente y fecha | Sí | «Junta de Andalucía – REDIAM» y la fecha |
| Navarra, IDENA | Geoportal de IDENA: CC BY 4.0; el WFS dice «no conditions apply» | Probable, sin confirmar para esta capa | «Gobierno de Navarra – IDENA» |
| Castilla-La Mancha | Aviso legal de castillalamancha.es: Ley 37/2007 citando la fuente | Sí (no lo excluye) | «JCCM – C.O.R. INFOCAM» |
| Valencia, VAERSA | Aviso legal de prevencionincendiosgva.es: «solo visualización y uso personal y no comercial» | **No**, sin autorización | — |
| La Rioja | Aviso legal de larioja.org: reproducción autorizada citando la fuente | Sin confirmar | «Gobierno de La Rioja» |

**Valencia no se puede usar en un producto comercial**: sirve solo para análisis
interno mientras no haya autorización de la Conselleria o VAERSA. Pla Alfa,
Navarra y La Rioja hay que confirmarlas antes de mostrarlas en la web. El texto de
cada una va en el metadato `licencia` de cada fichero.
