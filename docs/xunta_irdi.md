# IRDI de la Xunta de Galicia

Índice de risco diario de incendio forestal de la Consellería do Medio Rural:
<https://mediorural.xunta.gal/gl/temas/defensa-monte/irdi>. Se guarda para
compararlo con el riesgo que calcula VIGÍA en Galicia. **No hay archivo**: el
servidor solo tiene los 4 días en curso, así que lo que no se baja se pierde.
Guardando cada día los cuatro horizontes queda también el histórico de la
previsión a 4 días.

## Qué es

- FWI canadiense. Temperatura, humedad y viento a las 12 UTC del WRF de
  MeteoGalicia (1 km); lluvia de 24 h de estaciones de MeteoGalicia y radar de
  Cuntis. Todo interpolado a 200 m.
- Por concello (313) se toma el percentil 50 del mapa y se pasa a cinco
  niveles. Solo se publica el nivel, no el valor del FWI.
- Escala actual (leyenda de 22/07/2024): baixo (1) <12, moderado (2) 12–24,
  alto (3) 24–38, moi alto (4) 38–50, extremo (5) ≥50. La anterior (PLADIGA
  2024) era 0–3, 3–13, 13–23, 23–28 y >28: ojo al comparar con años previos.
- Se publica una vez al día, hacia las 08:48 UTC (medido el 07/11/2026, en
  horario de invierno), para hoy y los tres días siguientes. Antes de esa hora
  todo es de la víspera.

## Cómo se baja (job `irdi`, workflow `xunta_irdi.yml`)

Cuatro pasadas al día: 07:15, 09:15, 11:15 y 15:15 UTC. En cada una:

1. `GET` de la página: «Actualizado o Sábado, 07 de Novembro de 2026 ás 09:48»
   (hora de Galicia) y las pestañas `#dia_1` … `#dia_4` («Sábado 07»; el mes y
   el año salen de «Actualizado o»).
2. `GET` de los mapas `/sites/default/files/mapa-irdi/irdi_d0.jpg` … `d3.jpg`
   (966×989, sin georreferenciar). Se guardan si han cambiado.
3. Si esa hora de «Actualizado o» ya está guardada, se acaba aquí (5 peticiones).
   Si no, la tabla de cada día por el `views/ajax` de Drupal:

   ```
   POST https://mediorural.xunta.gal/gl/views/ajax?_wrapper_format=drupal_ajax&page=P
   view_name=tabla_irdi&view_display_id=block_1&view_args=dia_N&view_path=/node/1161&pager_element=0
   ```

   `page` va en la URL (en el cuerpo se ignora). Responde una lista de comandos
   de Drupal; el HTML de la tabla va en el `data` del primer `insert`. 50 filas
   por página; la 6 trae 13, así que son 7 páginas y 313 filas por día, 28 POST.
4. `GET` de la página otra vez, para comprobar que no se ha actualizado
   mientras tanto.

Entre peticiones hay 1,5 s de pausa, y cada una se reintenta hasta 3 veces con
espera creciente. User-Agent `fuego-datos/1.0 (+https://github.com/JuanUtrilla/fuego-datos)`.
Una pasada completa son 34 peticiones y menos de un minuto; las demás del día,
5. `robots.txt` no prohíbe nada de esto.

No funcionan (07/11/2026): `items_per_page=All`, `?_format=json`, `/jsonapi`
ni los otros displays de la vista (403). `view_args=all` da los 4 días
mezclados (1.252 filas) y no se usa.

## Qué se guarda

En `xunta/irdi/AAAA/MM/DD/`:

| Fichero | Qué |
|---|---|
| `irdi_<AAAAMMDDhhmm>.csv` | Parseo: `fecha_validez, dia (1-4), actualizado, codigo_ine, concello, nivel, nivel_nome`, 4 × 313 filas |
| `irdi_<AAAAMMDDhhmm>_bruto.tar.gz` | Bruto: `pagina.html`, los 28 `dia_N_pP.json`, `pagina_fin.html` y `peticiones.json` (hora, URL, cuerpo, código HTTP, bytes, Last-Modified y User-Agent de cada petición) |
| `irdi_<AAAAMMDD válido>_d<k>.jpg` | Mapa de hoy + k días, con su `Last-Modified` en los metadatos |
| `irdi_fallo_<hhmmss>.tar.gz` | Solo si algo falla: lo bajado hasta ese momento, para ver qué ha cambiado |

- `AAAAMMDDhhmm` es la hora de «Actualizado o», y la carpeta del CSV y del
  bruto es la de esa fecha; la de los mapas, la de la descarga.
- La tabla se compara con lo último guardado por el CSV (el HTML y los JSON
  cambian en cada petición por los identificadores de Drupal). Si la Xunta
  corrigiera algo sin cambiar la hora, no se vería: solo se vuelve a pedir la
  tabla cuando cambia «Actualizado o».
- El código INE sale de `fuego_datos/recursos/irdi_concellos.csv` (nombre tal
  como lo escribe la Xunta → código del padrón 2025 del INE). Casan los 313, y
  coinciden con los del WFS INSPIRE del IGN (`nationalCode[6:11]`).
- Unos 0,8 MB al día.
- Para comparar con el modelo, el «IRDI de hoy» es `dia = 1` del CSV de cada
  día; `dia` 2 a 4 sirven para ver cuánto cambia la previsión.

## Comprobaciones

El job falla (y `estado`/`vigilancia` avisan) si:
- algún día no da exactamente 313 concellos distintos;
- algún nivel no es uno de los cinco (se mira el texto y, si no, la clase CSS
  `irdi-…`; las de moi alto y extremo no se han visto todavía);
- las 50 filas de `dia_1` que trae la página no son las mismas que las del
  `views/ajax`;
- «Actualizado o» cambia entre el principio y el final (la siguiente pasada lo
  arregla);
- falta «Actualizado o» o no están las cuatro pestañas.

**Copias atrasadas de los JPG.** El 07/11/2026 `irdi_d0.jpg` alternaba entre la
versión de ese día (Last-Modified 08:47 UTC) y la del 06/11, según el servidor
que contestara. Un JPG con Last-Modified de más de 6 h antes de «Actualizado o»
se descarta (queda en el registro). Dentro de una pasada la sesión guarda la
cookie del balanceador, así que la página y la tabla salen del mismo servidor.

## Certificado

`mediorural.xunta.gal` no manda el certificado intermedio, así que requests y
curl fallan al verificar. No se desactiva la verificación: se usan las CA de
certifi más el intermedio, guardado en
`fuego_datos/recursos/globalsign_rsa_ov_ssl_ca_2018.pem` (bajado de
<http://secure.globalsign.com/cacert/gsrsaovsslca2018.crt>, SHA-256
`B6:76:FF:A3:…:76:4A`). **Caduca el 21/11/2028**: si el servidor cambia de
certificado o el intermedio caduca, el job fallará por SSL y habrá que
sustituir ese fichero por el que indique «CA Issuers» en el certificado del
servidor (`openssl s_client -connect mediorural.xunta.gal:443 | openssl x509 -noout -ext authorityInfoAccess`).

## Licencia y cita

Aviso legal de mediorural.xunta.gal: reutilización permitida, también
comercial, sin desnaturalizar el contenido y citando la fuente:
«Xunta de Galicia – Consellería do Medio Rural, IRDI».

## Histórico

No hay en el servidor ni en abierto (Wayback solo tiene unas pocas capturas
parciales). Para tener el IRDI pasado hay que pedirlo a la Consellería do Medio
Rural (acceso a información pública) o aproximarlo con el WRF de MeteoGalicia.
