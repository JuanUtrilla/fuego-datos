# fuego-datos

Descarga diaria de datos meteorológicos y de incendios para España y Portugal,
solo de fuentes abiertas cuya licencia permite el uso comercial. Este
repositorio solo tiene código: los datos van a un bucket privado de
Cloudflare R2 y, una vez por semana, a un disco local.

## Qué se baja

| Job | Fuente | Qué | Malla | Paso | Cada cuánto | Al día |
|---|---|---|---|---|---|---|
| `ecmwf` | IFS de ECMWF (datos abiertos, copia en AWS) | 33 variables: superficie, suelo y niveles de 850 y 700 hPa | 0,25° (0,1° en cuanto ECMWF la publique) | 3 h, hasta 144 h | 4 pasadas | ~17 MB |
| `icon-eu` | ICON-EU del DWD | 23 variables: superficie, rayos, altura de mezcla y suelo | 0,0625° (~7 km) | 1 h hasta 78 h, luego 3 h | 8 pasadas | ~260 MB |
| `era5land` | ERA5-Land (Copernicus CDS) | 16 variables, GRIB tal cual | 0,1° (~9 km) | 1 h | 1 día, con ~5 de retraso | ~7 MB |
| `aemet-obs` | AEMET | Observación de todas las estaciones (~856) | Puntos | 1 h | Cada 3 h (ventana de 12 h) | ~4 MB |
| `aemet-pred` | AEMET | Predicción horaria (48 h) y diaria (7 días) de los 8.124 municipios | Puntos | 1 h / 1 día | Cada 3 h, si cambia | ~20 MB |
| `aemet-productos` | AEMET | Mapas de riesgo de incendio (+1 a +7 días), avisos CAP, HARMONIE-AROME | Imagen / 2,5 km | — | Cada 6 h, si cambia | ~35 MB |
| `firms` | NASA FIRMS | Focos VIIRS (SNPP, NOAA-20, NOAA-21) y MODIS, últimas 48 h | 375 m / 1 km | — | Cada 3 h, si cambia | <1 MB |
| `effis` | EFFIS | Perímetros de la temporada en curso, ES y PT | Polígonos | — | 5 veces al día, si cambia | ~7 MB |
| `miteco` | MITECO | Partes de intervenciones definitivo y provisional (PDF) | — | — | 5 veces al día, si cambia | ~2 MB |

Los tamaños son los medidos el 17/10/2026. En total, unos 350 MB al día; unos
2,5 GB a la semana.

Dominio: rectángulo N 44,2 / S 35,6 / O −10,0 / E 4,6 (península, Baleares y
Portugal con margen). Las mallas se guardan enteras dentro del rectángulo, en
sus puntos nativos y sin interpolar; recortar a tierra o a un país se hace
después, en el cálculo. No hace falta elegir nodos al descargar.

Formato:
- **ECMWF e ICON-EU:** se recortan al dominio y se guardan en NetCDF comprimido, con la precisión de `rejilla.PRECISION` (0,1 K, 0,1 m/s, 1 % de humedad, 0,01 mm). Guardarlos enteros multiplicaría el volumen por cien.
- **Todo lo demás:** se guarda tal como lo entrega la fuente.

Lo que no tiene archivo en origen y se pierde si no se baja ese día: ICON-EU
(el DWD guarda 24 h), todo lo de AEMET y los partes del MITECO. ECMWF guarda
2–3 días en su portal, pero su copia en AWS conserva las pasadas desde 2023.
ERA5-Land, FIRMS y EFFIS se pueden volver a pedir.

## Licencias y citas

Todas permiten uso comercial. La web y cualquier producto tienen que llevar
estas citas e indicar que los datos se han modificado:

| Fuente | Licencia | Cita |
|---|---|---|
| ECMWF | CC BY 4.0 | «Contiene datos de previsión de ECMWF IFS. © ECMWF, CC BY 4.0. Datos modificados» |
| DWD | CC BY 4.0 | «Fuente: Deutscher Wetterdienst (DWD), CC BY 4.0. Datos modificados» |
| ERA5-Land | CC BY 4.0 | «Contiene información modificada del Copernicus Climate Change Service (año)» |
| AEMET | Ley 37/2007, uso comercial permitido | «Fuente: AEMET». Sin dar a entender que AEMET patrocina el producto |
| EFFIS | CC BY 4.0 | «© Unión Europea, Copernicus EMS – EFFIS. Datos modificados» |
| MITECO | Ley 37/2007 | «Fuente: Ministerio para la Transición Ecológica y el Reto Demográfico», con la fecha del parte |
| FIRMS | Sin restricciones; pide cita | «We acknowledge the use of data from NASA's FIRMS, part of NASA's ESDIS» |

Fuera, por licencia: la API gratuita de Open-Meteo (solo uso no comercial) y
el IPMA (prohíbe el uso con fines lucrativos sin permiso).

## Dónde queda cada cosa

```
<fuente>/<producto>/<aaaa>/<mm>/<dd>/<fichero>          datos
manifiestos/<fuente>/<aaaa>/<mm>/<dd>/<hhmmss>_<job>.json   lo que guardó cada ejecución
estado/                                                   última huella por producto y latido de cada job
```

Cada objeto lleva su SHA-256 en los metadatos. El manifiesto se escribe al
final de cada ejecución: si un job muere a medias, no deja manifiesto.

## Cómo funciona

1. **GitHub Actions** ejecuta cada job según su horario (`.github/workflows/`). Cada job mira qué hay ya en el bucket y baja solo lo que falta, así que un retraso o un fallo se recuperan solos en la siguiente ejecución.
2. **El servidor** lee del bucket con un token de solo lectura.
3. **El ordenador personal**, cada domingo, ejecuta `sincronizar`:
   - copia al disco todo lo nuevo y comprueba cada huella;
   - borra del bucket solo lo que tiene más de 7 días y está copiado en todos los destinos;
   - si un destino no está montado o hay un error de copia, no borra nada.
4. **`vigilancia.yml`** comprueba cada mañana que todas las fuentes están al día. Si alguna no lo está, el workflow falla y GitHub manda un correo.
5. **`mantener_activo.yml`** reactiva los workflows cada mes, porque GitHub los desactiva tras 60 días sin commits.

## Uso

Entorno de conda:

```bash
conda env create -f environment.yml
conda activate fuego_datos
cp .env.ejemplo .env      # y rellenar las claves
```

Sin las variables de R2, todo se guarda en `datos_locales/`. Sirve para probar.

```bash
python -m fuego_datos bajar aemet-obs firms        # uno o varios jobs
python -m fuego_datos estado                       # ¿está todo al día?
python -m fuego_datos sincronizar --simulacion     # qué copiaría y borraría
python -m fuego_datos sincronizar
python -m pytest -q
```

Timer semanal en el ordenador personal (necesita en `.env` el token
`bruto-portatil` y `DESTINO_ARCHIVO`):

```bash
sistema/instalar.sh
```

## Secretos de GitHub

`R2_ACCOUNT_ID`, `R2_BRUTO_ACCESS_KEY_ID`, `R2_BRUTO_SECRET_ACCESS_KEY`,
`AEMET_API_KEY`, `FIRMS_MAP_KEY` y `CDSAPI_KEY`. En GitHub los jobs llevan
`EXIGIR_R2=1`: si faltan las claves del bucket, fallan en vez de guardar en
el runner, donde se perdería todo.
