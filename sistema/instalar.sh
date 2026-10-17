#!/usr/bin/env bash
# Instala el timer semanal de sincronización en este ordenador (usuario, sin sudo).
# Antes: .env con las claves de R2 del token «bruto-portatil» y DESTINO_ARCHIVO.
set -euo pipefail
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$AQUI/.."
grep -q '^R2_ACCESS_KEY_ID=.\+' .env || { echo "Falta R2_ACCESS_KEY_ID en .env"; exit 1; }
echo "Prueba en simulación (no copia ni borra):"
conda run --no-capture-output -n fuego_datos python -m fuego_datos sincronizar --simulacion
mkdir -p ~/.config/systemd/user
cp "$AQUI"/fuego-sincronizar.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now fuego-sincronizar.timer
systemctl --user list-timers fuego-sincronizar.timer
echo "Registro: journalctl --user -u fuego-sincronizar"
