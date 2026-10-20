#!/usr/bin/env python3
"""Pasa un PDF de texto a Markdown legible, página a página, con las tablas
como bloques de texto con su maquetación (para no perder columnas).

Uso:
    python docs/pdf_a_md.py docs/ManualTecnico_WEB_API_SIAR_v2.2.pdf docs/siar_api_manual.md
"""
import re
import sys

import pymupdf


def main(pdf, md):
    doc = pymupdf.open(pdf)
    partes = [f"# {doc.metadata.get('title') or pdf}\n",
              f"Convertido de `{pdf}` ({doc.page_count} páginas). Texto tal cual; las tablas "
              "van en bloques de código para conservar las columnas.\n"]
    for i, pag in enumerate(doc, 1):
        partes.append(f"\n## Página {i}\n")
        tablas = pag.find_tables()
        zonas = [t.bbox for t in tablas.tables]
        for t in tablas.tables:
            filas = t.extract()
            partes.append("\n| " + " | ".join((c or "").replace("\n", " ") for c in filas[0]) + " |")
            partes.append("|" + "---|" * len(filas[0]))
            for f in filas[1:]:
                partes.append("| " + " | ".join((c or "").replace("\n", " ") for c in f) + " |")
            partes.append("")
        # el resto del texto, sin repetir lo que ya está en las tablas
        for b in pag.get_text("blocks"):
            x0, y0, x1, y1, texto = b[:5]
            dentro = any(x0 >= z[0] - 1 and y0 >= z[1] - 1 and x1 <= z[2] + 1 and y1 <= z[3] + 1 for z in zonas)
            texto = re.sub(r"[ \t]+", " ", texto).strip()
            if texto and not dentro:
                partes.append(texto + "\n")
    open(md, "w").write("\n".join(partes))
    print(f"{md}: {doc.page_count} páginas")


if __name__ == "__main__":
    main(*sys.argv[1:3])
