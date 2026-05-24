# NaviPort v0.3.0 Beta (PortMaster Edition)

NaviPort es un cliente nativo, ultraligero y de alto rendimiento hecho específicamente para la plataforma PortMaster. Está diseñado para reproducir música en streaming desde servidores Navidrome/Subsonic directamente en consolas portátiles ARM Linux bajo entornos de emulación (Knulli, Rocknix, ArkOS (?) etc.).

## Novedades en la v0.3.0 (Estabilidad y Controles Universales)

* Soporte Universal de Mandos (gptokeyb): Se ha abandonado la lectura directa del hardware en favor de la herramienta nativa gptokeyb. Mediante el archivo de configuración naviport.gptk, los controles son ahora 100% compatibles con cualquier hardware soportado por PortMaster (Anbernic, Powkiddy, Trimui, etc.).
* Prevención de Cuelgues (Deadlocks) en KMSDRM: Se ha introducido un blindaje a nivel de variables de entorno de SDL2 (SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS="0") que evita bloqueos gráficos cuando el sistema operativo intenta tomar el control de la pantalla para mostrar atajos físicos (ej. subir brillo o volumen).
* Cierre Seguro del Sistema: Se ha anulado el cierre gráfico por software que causaba bloqueos de vídeo. Ahora la salida se delega al frontend nativo (SIGKILL) usando la combinación de emergencia estándar, limpiando procesos de audio en segundo plano.
* Arreglado el cambio de brillo: que hacia que chraseará la aplicación

## Características Técnicas

* Motor Asíncrono (Daemon Threads): Las llamadas HTTP y la descarga de carátulas se ejecutan en hilos secundarios. Esto elimina los cuellos de botella de red y garantiza una interfaz (Pygame) fluida y sin congelaciones.
* Memoria de Navegación (State Stack): Pila de historial integrada. Al retroceder entre menús, el sistema recuerda instantáneamente tu posición, la lista cargada y el nivel exacto de scroll.
* Telemetría en Tiempo Real: Lector asíncrono que muestra el porcentaje de batería real del dispositivo (mediante lectura directa en sysfs o llamadas de sistema) y un reloj actualizados.
* Marquee Engine 2.0: Renderizado de texto animado cíclico. Tanto los títulos en las listas como el HUD inferior desplazan horizontalmente los nombres largos para asegurar la legibilidad total.
* Arquitectura Híbrida e IPC Sockets: Renderiza la interfaz directamente al Framebuffer y delega el flujo de streaming de audio a mpv nativo de Linux. El control de reproducción (Play/Pausa) y la lectura del progreso se realizan mediante comunicación bidireccional por sockets UNIX (/tmp/naviport_mpv_socket), logrando precisión milimétrica. Control de audio preciso mediante comunicación bidireccional por sockets UNIX (/tmp/naviport_mpv_socket) con el proceso nativo mpv.
* Diseño Zero-Write: El software no realiza escrituras persistentes en la tarjeta SD durante la ejecución (las carátulas se cachean directamente en la RAM). Ideal para prolongar la vida útil del almacenamiento en consolas portátiles.

## Estructura de Navegación y Controles

### Menú Principal
* Álbumes: Filtrado rápido por selección Aleatoria, Recientes o Alfabético.
* Artistas: Índice alfabético completo.
* Playlists: Acceso a tus listas de reproducción personales.

### Navegación General
* Cruceta Arriba / Abajo: Moverse de 1 en 1 por los menús o listas. Mantenlo pulsado para activar el scroll acelerado.
* Cruceta Izquierda / Derecha:
    * En menús principales: Salto de página rápido (bloques de 10).
    * En lista de canciones: Salto a la pista anterior o siguiente, interrumpiendo el flujo sonoro actual.

### Acciones de Botones (Editables desde naviport.gptk)
* Botón Aceptar: Entrar a la sección / Cargar contenido / Reproducir selección.
* Botón Volver: Volver atrás a la pantalla anterior.
* Botón Select: Activar / Desactivar Modo Aleatorio (Shuffle).
* Botón Start (o Botón Asignado a Pausa): Pausar / Reanudar música.
* Select + Start: Salir de NaviPort (Cierre forzoso seguro, mata el proceso limpiamente sin congelar el buffer de vídeo).

### Control de Volumen y Sistema
* Botones L1 / R1: Bajar o subir el volumen del sistema un 5%.
* Atajos del Sistema Operativo: Usar los atajos nativos (ej. Función + Volumen / Brillo) ya es 100% seguro y no colapsará la aplicación.

## Estructura de Archivos

```bash
roms/
└── ports/
    ├── NaviPort.sh                # Script iniciador
    └── naviport/
        ├── main.py                # Núcleo del programa
        ├── config.json            # Credenciales del servidor
        └── README.md              # Este manual

```
## Configuración Inicial

Edita el archivo naviport/config.json con los datos de tu servidor:

{
    "server_url": "http://192.168.1.X:4533",
    "username": "tu_usuario",
    "password": "tu_contraseña"
}

*Nota: Para evitar que la pantalla se atenúe durante la escucha, asegúrate de configurar el "Tiempo de salvapantallas" en 0 (o "Nunca") desde las opciones de configuración de tu sistema operativo.*
