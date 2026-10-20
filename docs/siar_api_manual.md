# SiAR API: Manual de usuario

Convertido del manual oficial (https://servicio.mapa.gob.es/siarweb/documentos/manuales/ManualTecnico_WEB_API_SIAR.pdf, 17 páginas) con `docs/pdf_a_md.py`. Texto tal cual; las tablas en formato Markdown.


## Página 1

Manual Web
API SiAR

Versión 2.2
Mayo 2025


## Página 2

www.siar.es

Página 2 de 17

Índice

Descripción de los servicios de la Web API............................................................................................3

Acceso a la Web API............................................................................................................................3

Consideraciones generales..................................................................................................................4

Servicio Obtener Token........................................................................................................................4

1. Cifrado del identificador del usuario:....................................................................................5

2. Cifrado de la contraseña:.......................................................................................................5

3. Obtención del token:.............................................................................................................5

Servicio Info.........................................................................................................................................6

Descripción general....................................................................................................................6

Parámetros de entrada...............................................................................................................6

Valores de salida.........................................................................................................................6

Servicio Datos....................................................................................................................................11

Parámetros de ruta..................................................................................................................11

Parámetros de consulta............................................................................................................12

Valores de salida.......................................................................................................................13


## Página 3

www.siar.es

Página 3 de 17

Descripción de los servicios de la Web API

API SiAR ofrece flexibilidad y autonomía a los usuarios mediante un servicio HTTPS que devuelve los 
datos en formato JSON. Lenguajes como JavaScript, Java o C# permiten fácilmente realizar peticiones y 
procesar las respuestas.

Acceso a la Web API

La solicitud de acceso a la Web API SiAR puede realizarse de dos maneras:

• Usuarios nuevos del SiAR: Solicitando el acceso en el proceso de alta como usuario en el SiAR,
seleccionando la opción “Sí” en el desplegable “Deseo darme de alta en API SiAR”

Formulario de alta de usuario en donde se piden los datos de NIF / NIE, nombre, primer apellido, segundo apellido, correo electrónico y la opción de darse de alta o no en API SiAR

• Usuarios ya registrados en SiAR: para los usuarios ya registrados que no hubieran solicitado el alta en
API SiAR en el momento del registro, podrán realizar la solicitud a través de la opción “Editar perfil”
que se encuentra dentro de “Mi SiAR”, seleccionando el valor “Sí” dentro en el desplegable “Deseo
darme de alta en API SiAR”


## Página 4


| Entorno | URL | Observaciones |
|---|---|---|
| Producción | https://servicio.mapa.gob.es/siarapi |  |

www.siar.es

Página 4 de 17

Consideraciones generales

URL de los servicios de la Web API.

A continuación, se presenta la URL de API SiAR:

A lo largo del documento, esta URL se indicará mediante el término {URL_Base}.

Servicios protegidos por token

Para acceder a los servicios ofrecidos por la Web API de SIAR, es necesario contar con un token de 
autenticación. Este token identifica de forma única al usuario. Todas las peticiones a servicios protegidos 
deben incluir el token como parámetro en la URL, utilizando la clave token.

Para obtener el token, la Web API dispone de un conjunto de métodos específicos que permiten generar 
y recuperar esta información, los cuales se describen en el apartado correspondiente.

Ejemplo:

Supongamos que un usuario ha obtenido el siguiente token ficticio: 1Ab.

Si desea invocar un servicio cuya ruta es: {URL_Base}/API/V1/ServicioAPI?token={token}, la URL 
completa sería

https://servicio.mapa.gob.es/siarapi/API/V1/ServicioAPI?token=1Ab

Importante: En los ejemplos se usará el token ficticio y no funcional 1Ab

Servicio Obtener Token

Para acceder a las funcionalidades protegidas que expone la Web API de SIAR, es obligatorio disponer 
de un token de autenticación. Este token identifica de forma única al usuario, permitiendo controlar el 
acceso y uso de los servicios.

La Web API proporciona una serie de métodos que permiten obtener dicho token de forma segura. A 
continuación, se describen los pasos que deben seguirse de manera secuencial:


## Página 5

www.siar.es

Página 5 de 17

1. Cifrado del identificador del usuario:

El usuario debe estar previamente dado de alta en el sistema REGEUS del MAPA.

Para cifrar el identificador (por ejemplo, un NIF), se utiliza el siguiente método de la API:

Método HTTP: GET

Endpoint: {BaseURL}/API/V1/Autenticacion/cifrarCadena?cadena={NIF}

Este método devuelve una cadena cifrada del identificador que se utilizará en el paso 3.

2. Cifrado de la contraseña:

Del mismo modo, la contraseña asociada al usuario debe ser cifrada utilizando el mismo método del 
paso 1:

Método HTTP: GET

Endpoint: {BaseURL}/API/V1/Autenticacion/cifrarCadena?cadena={password}

Este método devuelve una cadena cifrada del identificador que se utilizará en el paso 3.

3. Obtención del token:

Este método requiere como entrada las dos cadenas cifradas obtenidas en los pasos anteriores y, si la 
autenticación es correcta, devuelve el token que permitirá acceder a los servicios protegidos.

Una vez cifrados el identificador (paso 1) y la contraseña asociada a dicho identificador (paso 2), se debe 
invocar el siguiente servicio para obtener el token de autenticación:

Método HTTP: GET

Endpoint: {BaseURL}/API/V1/Autenticacion/obtenerToken ?Usuario={DNI_cifrado_en_
paso_1}&Password={contraseña_cifrada_en_paso_2}


## Página 6


| Nombre | Obligatorio | Descripción |
|---|---|---|
| PeticionInfo | Sí | Admite uno de los siguientes valores: • CCAA • PROVINCIAS • ESTACIONES • ACCESOS • CODIGOSVALIDACION Para obtener una descripción detallada de los datos que devuelve el servicio en cada caso, consulte el apartado “Valores de salida”. |
| token | Sí | Token del usuario obtenido a través del servicio “Obtener Token” |

www.siar.es

Página 6 de 17

Servicio Info

Descripción general

El servicio Info permite obtener datos de referencia utilizados por otras operaciones de la Web API. Su 
objetivo principal es ofrecer información estructurada que facilita la configuración y parametrización de 
las solicitudes realizadas por el usuario, como, por ejemplo: qué comunidades autónomas, provincias, 
estaciones, términos municipales o códigos de validación están disponibles en el sistema.

Método HTTP: GET

Endpoint: {BaseURL}/API/V1/Info/{PeticionInfo}?token={token}

Parámetros de entrada

Valores de salida

Los valores de salida dependen del valor del parámetro de entrada PeticionInfo.

• Si PeticionInfo = CCAA

» Devuelve un listado en formato JSON de las comunidades autónomas. Tiene la siguiente

estructura:


## Página 7


| Campo | Tipo | Descripción |
|---|---|---|
| CCAA | String | Nombre de la comunidad autónoma |
| Código | String | Código de tres letras que identifica a la C.A. para su uso en los servicios de la Web API. |


| Campo | Tipo | Descripción |
|---|---|---|
| Provincia | String | Nombre de la provincia |
| Codigo | String | Código que identifica a la provincia para su uso en los servicios de la Web API. |
| Codigo CCCAA _ | String | Código de tres letras que identifica a la C.A. |
| IdProvincia | Integer | ID numérico |

www.siar.es

Página 7 de 17

• Ejemplo de petición:

https://servicio.mapa.gob.es/siarapi/API/V1/Info/CCAA?token=1Ab

• Ejemplo de respuesta:

{ "datos": [

{ "CCAA": "Andalucía", "Codigo": "AND" },

{ "CCAA": "Aragón", "Codigo": "ARA" }

], "MensajeRespuesta": null}

• Si PeticionInfo = PROVINCIAS

» Devuelve un listado en formato JSON de las provincias y su relación con la comunidad autónoma.

Tiene la siguiente estructura:

» Ejemplo de petición:

https://servicio.mapa.gob.es/siarapi/API/V1/Info/PROVINCIAS?token=1Ab

» Ejemplo de respuesta:

{ "datos": [

{ "Provincia": "Cáceres", "Codigo": "CC", "Codigo_CCAA": "EXT", "IdProvincia": 10 }

], "MensajeRespuesta": null}


## Página 8


| Campo | Tipo | Descripción |
|---|---|---|
| Estacion | String | Nombre de la estación metereológica |
| Codigo | String | Código identificador de la estación (único dentro de la provincia), para su uso en los servicios de la Web API. |
| Termino | String | Nombre del término municipal donde está ubicada la estación. Código de tres letras que identifica a la C.A. |
| Longitud | String | Coordenada de longitud en formato DMS (grados, minutos, segundos). |
| Latitud | String | Coordenada de latitud en formato DMS (grados, minutos, segundos). |
| Altitud | Integer | Altura de la estación sobre el nivel del mar (en metros). |
| XUTM | Integer | Coordenada UTM en el eje X (Este). |
| YUTM | Integer | Coordenada UTM en el eje Y (Norte). |
| Huso | Integer | Huso geográfico UTM correspondiente. |
| Fecha Instalacion _ | String | Fecha de instalación de la estación (formato ISO 8601). |
| Fecha Baja _ | String | Fecha de baja de la estación (si procede). Puede ser null si está activa. |
| Red estacion _ | String | Literal que indica si la estación es del ministerio o de C.A. |
| IdProvincia | Integer | ID numérico que identifica a la provincia para su uso en los servicios de la Web API. |
| IdEstacion | Integer | Identificador interno único de la estación. |

www.siar.es

Página 8 de 17

• Si PeticionInfo = ESTACIONES

» Devuelve un listado en formato JSON con la información detallada de las estaciones

meteorológicas. Tiene la siguiente estructura.

» Ejemplo de petición:

https://servicio.mapa.gob.es/siarapi/API/V1/Info/ESTACIONES?token=1Ab


## Página 9


| Campo | Tipo | Descripción |
|---|---|---|
| NumAccesosMinutoActual | Integer | Número de peticiones realizadas por el usuario en el último minuto. |
| MaxAccesosMinuto | Integer | Número máximo de peticiones permitidas por minuto con el token actual. |
| NumAccesosDiaActual | Integer | Número total de peticiones realizadas en el día actual. |
| MaxAccesosDia | Integer | Número máximo de peticiones permitidas por día. |
| RegistrosAcumuladosMinuto | Integer | Número total de registros descargados en el último minuto. |
| MaxRegistrosMinuto | Integer | Límite máximo de registros que pueden descargarse por minuto. |
| RegistrosAcumuladosDia | Integer | Total, de registros descargados en el día actual. |
| MaxRegistrosDia | Integer | Límite máximo de registros que se pueden descargar en un solo día. |

www.siar.es

Página 9 de 17

» Ejemplo de respuesta:

{ "datos": [ {

"Estacion": "Tarazona",

"Codigo": "AB01",

"Termino": "Tarazona de la Mancha",

"Longitud": "015512000W",

"Latitud": "391520000N",

"Altitud": 722,

“XUTM”: 593160,

“YUTM”: 4345720,

“Huso”: 30,

“Fecha_Instalacion”: “1999-10-12T22:00:00”,

“Fecha_Baja”: null,

“Red_Estacion”: “Red de estaciones del Ministerio”,

“IdProvincia”: 2,

“IdEstacion”: “1” }], “MensajeRespuesta”: null}

• Si PeticionInfo = ACCESOS

» Devuelve un objeto JSON con la información sobre los límites y el uso actual del token. Tiene la

siguiente estructura:


## Página 10


| Campo | Tipo | Descripción |
|---|---|---|
| Descripción | String | Texto explicativo del estado o nivel de validación aplicado al dato. |
| IdCodigoValidacion | String | Código numérico (como cadena de texto) que identifica el tipo de validación. |

www.siar.es

Página 10 de 17

» Ejemplo de petición:

https://preservicio.mapa.gob.es/siarapi/API/V1/Info/ACCESOS?token=1Ab

» Ejemplo de respuesta:

{ "datos": [

{

"NumAccesosMinutoActual": 1,

"MaxAccesosMinuto": 30,

"NumAccesosDiaActual": 24,

"MaxAccesosDia": 1000,

"RegistrosAcumuladosMinuto": 0,

"MaxRegistrosMinuto": 1000000,

"RegistrosAcumuladosDia": 13142,

"MaxRegistrosDia": 1000000

} ], "MensajeRespuesta": null}

• Si PeticionInfo = CODIGOSVALIDACION

» Devuelve un listado en formato JSON con la descripción de los códigos de validación. Tiene la

siguiente estructura:

» Ejemplo de petición:

https://servicio.mapa.gob.es/siarapi/API/V1/Info/CODIGOSVALIDACION?token=1Ab

» Ejemplo de respuesta:

{"datos": [ {

"Descripcion": "Dato incorporado sin ningún error (Nivel 1)",

"IdCodigoValidacion": "100"

} ], "MensajeRespuesta": null}


## Página 11


| Nombre | Obligatorio | Descripción |
|---|---|---|
| tipoDatos | Sí | Define el tipo de datos solicitado. Admite uno de los siguientes valores: • Horarios • Diarios • Semanales • Mensuales |
| ambito | Sí | Determina el ámbito de consulta de los datos. Admite uno de los siguientes valores: • CCAA • PROVINCIA • ESTACION |

www.siar.es

Página 11 de 17

Servicio Datos

Este servicio permite consultar datos agroclimáticos en función del ámbito de consulta y el tipo de dato 
solicitado

Método HTTP: GET

Endpoint: {BaseURL}/API/V1/Datos/{tipoDatos}/{ambito}?<parámetros_de_consulta>

Parámetros de ruta


## Página 12


| Nombre | Obligatorio | Tipo | Descripción |
|---|---|---|---|
| token | Sí | String | Token de autenticación válido. |
| Id | Sí | Sí | Identificador de la CCAA, provincia o estación, según el valor de tipoDatos. El parámetro Id debe corresponder con el tipo de dato solicitado. Por ejemplo: • Si tipoDatos=ESTACION, el valor será el código de estación (HU01, AB02, etc.). • Si tipoDatos=PROVINCIA, será el código de la provincia (AB, VA, etc.). • Si tipoDatos=CCAA, será el código de comunidad autónoma (AND, CAT, etc.). Puede accederse a estos códigos mediante el servicio INFO, consultando el atributo ‘Codigo’ de la respuesta Si se quiere solicitar varios Id, deben separarse en la petición de la forma: …&Id={id1}&Id={id2}… |
| FechaInicial | Sí | String | Fecha de inicio del periodo de consulta (formato YYYY-MM-DD). |
| FechaFinal | Sí | String | Fecha de fin del periodo de consulta (formato YYYY- MM-DD). |
| FechaUltModifci acion | No | String | Fecha de última modificación del periodo de consulta (formato YYYY-MM-DD). |
| DatosCalculados | No | Boolean (true o false) | • Indica si se deben mostrar en los resultados, las variables calculadas. • Este parámetro solo aplica cuando el valor de tipoDatos es Diarios, Semanales o Mensuales. • Si no se indica nada, el valor por defecto es false. |

www.siar.es

Página 12 de 17

Parámetros de consulta

• Ejemplo de petición de datos horarios para la Comunidad Autónoma de Andalucía entre el
12/005/2025 y el 13/05/2025, solicitando datos códigos de validación:

https://servicio.mapa.gob.es/siarapi/API/V1/Datos/Horarios/CCAA?token=1Ab&Id=AND&FechaIni
cial=2025-01-12&FechaFinal=2025-05-13&DatosCalculados=true


## Página 13


| tipoDatos = “Horarios” |  |  |
|---|---|---|
| Campo | Tipo | Descripción |
| Fecha | String | Fecha del dato registrado |
| Estacion | String | Código Nemo de la estación |
| HorMin | Integer | Hora y minuto del dato (formato HHMM, por ejemplo, 1430). |
| TempMedia | Float | Temperatura media del aire (°C). |
| HumedadMedia | Float | Humedad relativa media (%). |
| VelViento | Float | Velocidad media del viento (km/h o m/s según configuración). |
| DirViento | Float | Dirección media del viento (grados). |
| Radiacion | Float | Radiación solar (W/m²). |
| Precipitacion | Float | Precipitación acumulada (mm). |
| TempSuelo1 | Float | Temperatura del suelo a nivel 1 (°C). |
| TempSuelo2 | Float | Temperatura del suelo a nivel 2 (°C). |
| IdProvincia | Integer | ID numérico que identifica a la provincia para su uso en los servicios de la Web API. |
| IdEstacion | Integer | Identificador interno único de la estación. |

www.siar.es

Página 13 de 17

• Ejemplo de petición de datos diarios para la Estaciones “HU01” y “HU02” entre 01/01/2025 y el
01/05/2025, solicitando datos calculados y códigos de validación:

https://servicio.mapa.gob.es/siarapi/API/V1/Datos/Diarios/ESTACION?token=1Ab&Id=HU01&Id=H
U02&FechaInicial=2025-01-01&FechaFinal=2025-05-01&DatosCalculados=true

Valores de salida

Los valores de salida dependen del valor del parámetro de ruta “tipoDatos”.

• Si tipoDatos = Horarios, el servicio devuelve una lista de registros con las variables meteorológicas
observadas en franjas horarias concretas. Los datos varían en función de la estación y el intervalo
solicitado.

• Tiene la siguiente estructura:


## Página 14


| tipoDatos = “Diarios” |  |  |
|---|---|---|
| Campo | Tipo | Descripción |
| Fecha | String | Fecha del dato registrado |
| Estacion | String | Código Nemo de la estación |
| TempMedia | Float | Temperatura media del aire (°C). |
| TempMax | Float | Temperatura máxima diaria (°C) |
| HorMinTempMax | Integer | Hora y minuto de la temperatura máxima (formato HHMM) |
| TempMin | Float | Temperatura mínima diaria (°C) |
| HorMinTempMin | Integer | Hora y minuto de la temperatura mínima (formato HHMM) |
| HumedadMedia | Float | Humedad relativa media diaria (%) |
| HumedadMax | Float | Humedad máxima diaria (%) |
| HorMinHumMax | Integer | Hora y minuto de la humedad máxima |
| humedadMin | Float | Humedad mínima diaria (%) |
| HorMinHumMin | Integer | Hora y minuto de la humedad mínima |
| VelViento | Float | Velocidad media diaria del viento |
| DirViento | Float | Dirección media del viento (grados) |
| VelVientoMax | Float | Velocidad máxima del viento (km/h o m/s) |
| HorMinVelMax | Integer | Hora y minuto de la velocidad máxima del viento |
| DirVientoVelMax | Float | Dirección del viento en el momento de máxima velocidad |
| Radiacion | Float | Radiación solar diaria acumulada (MJ/m² o W/m² según configuración) |
| Precipitacion | Float | Precipitación diaria total (mm) |
| TempSuelo1 | Float | Temperatura del suelo a nivel 1 |
| TempSuelo2 | Float | Temperatura del suelo a nivel 2 |
| IdProvincia | Integer | ID numérico que identfi ci a a la provincia para su uso en los servicios de la Web API. |
| IdEstacion | Integer | Identificador interno único de la estación. |


| DatosCalculados = “true” para tipoDatos = “Diarios” |  |  |
|---|---|---|
| Campo | Tipo | Descripción |
| EtPMon | Float | Evapotranspiración por Penman-Monteith |
| PePMon | Float | Precipitación efectiva por Penman-Monteith |

www.siar.es

Página 14 de 17

• Si tipoDatos = Diarios, el servicio devuelve un conjunto de registros con estadísticas meteorológicas
diarias por estación. Tiene la siguiente estructura:


## Página 15


| tipoDatos = “Diarios2” |  |  |
|---|---|---|
| Campo | Tipo | Descripción |
| IdProvincia | Integer | Identificador numérico de la provincia. |
| IdEstacion | String | Código identificador de la estación. |
| Año | Integer | Año del registro. |
| Dia | Integer | Día del año (1–365/366). |
| Calmas | Integer | Número de observaciones horarias sin viento (calmas). |
| NoCalmas | Integer | Número de observaciones horarias con viento (>0 m/s o km/h). |
| Temp 40a 30 _ _ | Integer | Número de horas con temperatura entre 40 °C y 30 °C. |
| Temp 30a 20 _ _ | Integer | Número de horas con temperatura entre 30 °C y 20 °C. |
| Temp 20a 10 _ _ | Integer | Número de horas con temperatura entre 20 °C y 10 °C. |
| Temp 10a 0 _ _ | Integer | Número de horas con temperatura entre 10 °C y 0 °C. |
| Temp 0a 10 _ _ | Integer | Número de horas con temperatura entre 0 °C y 10 °C. |
| Temp 10a 20 _ _ | Integer | Número de horas con temperatura entre 10 °C y 20 °C. |
| Temp 20a 30 _ _ | Integer | Número de horas con temperatura entre 20 °C y 30 °C. |
| Temp 30a 40 _ _ | Integer | Número de horas con temperatura entre 30 °C y 40 °C. |
| Temp 40a 50 _ _ | Integer | Número de horas con temperatura entre 40 °C y 50 °C. |
| Temp 50a 60 _ _ | Integer | Número de horas con temperatura entre 50 °C y 60 °C. |

www.siar.es

Página 15 de 17

• Si tipoDatos = Diarios2, el servicio permitirá buscar los datos diarios de las integrales térmicas y
calmas de las estaciones. Tiene la siguiente estructura:


## Página 16


| tipoDatos = “Semanales” |  |  |
|---|---|---|
| Campo | Tipo | Descripción |
| Año | Integer | Año natural del dato |
| Semana | Integer | Número de semana (1–52/53) |
| Estacion | String | Código nemo de la estación |
| TempMedia | Float | Temperatura media semanal (°C) |
| TempMax | Float | Temperatura máxima registrada durante la semana (°C) |
| DiaHorMinTempMax | String | Día y hora en que se registró la temperatura máxima (formato “DDHHMM”) |
| TempMin | Float | Temperatura mínima semanal (°C) |
| DiaHorMinTempMin | String | Día y hora de la temperatura mínima (formato “DDHHMM”) |
| HumedadMedia | Float | Humedad relativa media (%) |
| HumedadMax | Float | Humedad relativa máxima (%) |
| DiaHorMinHumMax | String | Día y hora de humedad máxima |
| HumedadMin | Float | Humedad relativa mínima (%) |
| DiaHorMinHumMin | String | Día y hora de humedad mínima |
| VelViento | Float | Velocidad media del viento semanal |
| DirViento | Float | Dirección media del viento (grados) |
| VelVientoMax | Float | Velocidad máxima del viento |
| DiaHorMinVelMax | String | Día y hora de velocidad máxima del viento |
| DirVientoVelMax | Float | Dirección del viento en la velocidad máxima |
| Radiacion | Float | Radiación solar acumulada semanal |
| Precipitacion | Float | Precipitación acumulada semanal (mm) |


| DatosCalculados = “true” para tipoDatos = “Semanales” |  |  |
|---|---|---|
| Campo | Tipo | Descripción |
| EtPMon | Float | Evapotranspiración por Penman-Monteith |
| PePMon | Float | Precipitación efectiva por Penman-Monteith |

www.siar.es

Página 16 de 17

• Si tipoDatos = Semanales, el servicio proporciona información climática a nivel de semana, incluyendo
medias, extremos y fechas asociadas, además de variables calculadas si se solicitan. Tiene la siguiente
estructura:


## Página 17


| tipoDatos = “Mensuales” |  |  |
|---|---|---|
| Campo | Tipo | Descripción |
| Año | Integer | Año natural del dato |
| Mes | Integer | Número del mes (1 a 12). |
| Estacion | String | Código nemo de la estación |
| TempMedia | Float | Temperatura media mensual (°C). |
| TempMax | Float | Temperatura máxima registrada en el mes (°C). |
| DiaHorMinTempMax | String | Día y hora de la temperatura máxima |
| TempMin | Float | Temperatura mínima registrada en el mes (°C). |
| DiaHorMinTempMin | String | Día y hora de la temperatura mínima |
| HumedadMedia | Float | Humedad relativa media mensual (%). |
| HumedadMax | Float | Humedad relativa máxima mensual (%). |
| DiaHorMinHumMax | String | Día y hora de la humedad máxima. |
| HumedadMin | Float | Humedad relativa mínima mensual (%). |
| DiaHorMinHumMin | String | Día y hora de la humedad mínima. |
| VelViento | Float | Velocidad media del viento mensual. |
| DirViento | Float | Dirección media del viento mensual (grados). |
| VelVientoMax | Float | Velocidad máxima del viento mensual. |
| DiaHorMinVelMax | String | Día y hora de la velocidad máxima. |
| DirVientoVelMax | Float | Dirección del viento en el momento de máxima velocidad. |
| Radiacion | Float | Radiación solar mensual acumulada (MJ/m² o W/m²). |
| Precipitacion | Float | Precipitación total mensual (mm). |


| DatosCalculados = “true” para tipoDatos = “Mensuales” |  |  |
|---|---|---|
| Campo | Tipo | Descripción |
| EtPMon | Float | Evapotranspiración por Penman-Monteith |
| PePMon | Float | Precipitación efectiva por Penman-Monteith |

www.siar.es

Página 17 de 17

• Si tipoDatos = Mensuales, el servicio proporciona información climática a nivel de semana,
incluyendo medias, extremos y fechas asociadas, además de variables calculadas si se solicitan. Tiene
la siguiente estructura:
