#!/bin/bash
# Nombre del Port: NaviPort

# 1. Cargar las herramientas estándar de PortMaster (necesario para gptokeyb)
XDG_DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
if [ -d "/opt/system/Tools/PortMaster/" ]; then
  controlfolder="/opt/system/Tools/PortMaster"
elif [ -d "/opt/tools/PortMaster/" ]; then
  controlfolder="/opt/tools/PortMaster"
elif [ -d "$XDG_DATA_HOME/PortMaster/" ]; then
  controlfolder="$XDG_DATA_HOME/PortMaster"
else
  controlfolder="/roms/ports/PortMaster"
fi
source $controlfolder/control.txt

# 2. Moverse a la carpeta del programa
# La variable $directory viene de control.txt y apunta a la partición de roms correcta
GAMEDIR="/$directory/ports/naviport"
cd "$GAMEDIR"

export PYTHONUNBUFFERED=1

# 3. Lanzar gptokeyb en segundo plano
# - Intercepta los controles de la consola.
# - El primer argumento es el nombre del proceso a monitorizar ("python3").
# - El parámetro -c le pasa tu archivo de configuración de mapeo.
$GPTOKEYB "python3" -c "./naviport.gptk" &

# 4. Ejecutar el script guardando errores
python3 main.py > log.txt 2>&1

# 5. Limpieza vital: Matar gptokeyb al salir
# Si no lo matas, los controles del menú de la consola (EmulationStation) se quedarán locos.
$KILLALL "gptokeyb"

# 6. Limpiar pantalla al salir
printf "\033c" > /dev/tty1