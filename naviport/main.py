import sys
import os
import json
import hashlib
import random
import string
import urllib.request
import urllib.parse
import traceback
import subprocess
import signal
import io
import pygame
import threading
import socket
import time
from datetime import datetime



# Variables de entorno críticas para KMSDRM / PortMaster
os.environ["SDL_VIDEO_ALLOW_SCREENSAVER"] = "0"
os.environ["SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS"] = "0" # <- NUEVO: Evita la congelación por Hotkeys
os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "0" # <- Apagamos esto para que no haya conflictos

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Estados de la aplicación
STATE_MENU_PRINCIPAL = "MENU"
STATE_LOADING = "LOADING"
STATE_ALBUMS = "ALBUMS"
STATE_ARTISTS = "ARTISTS"
STATE_PLAYLISTS = "PLAYLISTS"
STATE_TRACKS = "TRACKS"

# Opciones de menú principal
MENU_ITEMS = [
    {"title": "Álbumes (Aleatorios)", "action": "album_random"},
    {"title": "Álbumes (Recientes)", "action": "album_recent"},
    {"title": "Álbumes (Alfabético)", "action": "album_alpha"},
    {"title": "Artistas", "action": "artists"},
    {"title": "Playlists", "action": "playlists"}
]

# Control de memoria y caché
cover_cache = {}
MAX_CACHE_SIZE = 20
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
MPV_SOCKET = "/tmp/naviport_mpv_socket"

# Variables Globales
audio_process = None
is_audio_paused = False
async_state = {"status": "idle", "data": [], "error": None, "target_state": None}
cover_queue = []
nivel_bateria = "100%"

# --- TELEMETRÍA Y SISTEMA ---

def leer_bateria_sistema():
    rutas = [
        "/sys/class/power_supply/axp2202-battery/capacity",
        "/sys/class/power_supply/BAT0/capacity",
        "/sys/class/power_supply/battery/capacity",
        "/sys/class/power_supply/axp20x-battery/capacity",
        "/sys/class/power_supply/rk818-battery/capacity"
    ]
    
    # Método 1: Intento nativo y rápido en Python
    for ruta in rutas:
        try:
            if os.path.exists(ruta):
                with open(ruta, "r") as f:
                    valor = f.read().strip()
                    if valor: return f"{valor}%"
        except Exception:
            continue
            
    # Método 2: Fallback infalible (Ejecuta 'cat' igual que en SSH)
    for ruta in rutas:
        try:
            salida = subprocess.check_output(["cat", ruta], stderr=subprocess.DEVNULL)
            valor_cat = salida.decode('utf-8').strip()
            if valor_cat: return f"{valor_cat}%"
        except Exception:
            continue
            
    return "??"

def worker_actualizar_telemetria():
    global nivel_bateria
    while True:
        nivel_bateria = leer_bateria_sistema()
        time.sleep(30) # Actualiza la batería cada 30 segundos

# --- COMUNICACIÓN IPC CON MPV ---

def enviar_comando_mpv(comando):
    if not os.path.exists(MPV_SOCKET): return None
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.1)
        s.connect(MPV_SOCKET)
        msg = json.dumps({"command": comando}) + "\n"
        s.sendall(msg.encode('utf-8'))
        data = s.recv(1024)
        s.close()
        if data:
            return json.loads(data.decode('utf-8'))
    except Exception: pass
    return None

def obtener_tiempo_mpv():
    res = enviar_comando_mpv(["get_property", "time-pos"])
    if res and "data" in res:
        return float(res["data"])
    return 0.0

def alternar_pausa_sistema():
    global is_audio_paused
    res = enviar_comando_mpv(["get_property", "pause"])
    if res and "data" in res:
        estado_actual = res["data"]
        enviar_comando_mpv(["set_property", "pause", not estado_actual])
        is_audio_paused = not estado_actual
        return True
    return False

def detener_audio_sistema():
    global audio_process, is_audio_paused
    enviar_comando_mpv(["quit"])
    if audio_process:
        try: audio_process.wait(timeout=1)
        except subprocess.TimeoutExpired: audio_process.kill()
        except Exception: pass
    audio_process = None
    is_audio_paused = False
    if os.path.exists(MPV_SOCKET):
        try: os.remove(MPV_SOCKET)
        except: pass

def reproducir_stream_sistema(url):
    global audio_process, is_audio_paused
    detener_audio_sistema()
    cmd = [
        "mpv", "--vid=no", "--vo=null", "--cache=yes",
        "--demuxer-max-bytes=8MiB", "--cache-secs=15", "--quiet",
        f"--input-ipc-server={MPV_SOCKET}", url
    ]
    try:
        audio_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        is_audio_paused = False
        return True
    except Exception as e: return False

# --- LLAMADAS API SUBSONIC ---

def generar_url_api(endpoint, params={}):
    try:
        with open(CONFIG_PATH, "r") as f: config = json.load(f)
    except Exception: return None
    base_url = config["server_url"].rstrip("/")
    salt = "".join(random.choices(string.ascii_letters + string.digits, k=8))
    token = hashlib.md5((config["password"] + salt).encode('utf-8')).hexdigest()
    base_params = {"u": config["username"], "t": token, "s": salt, "v": "1.16.1", "c": "NaviPort", "f": "json"}
    base_params.update(params)
    return f"{base_url}/rest/{endpoint}?{urllib.parse.urlencode(base_params)}"

def fetch_albumes(tipo_filtro):
    size_request = 500 if tipo_filtro == "alphabeticalByName" else 50
    url = generar_url_api("getAlbumList2.view", {"type": tipo_filtro, "size": size_request})
    if not url: return []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'NaviPort'})
        with urllib.request.urlopen(req, timeout=8) as res:
            data = json.loads(res.read().decode())
            albums = data.get("subsonic-response", {}).get("albumList2", {}).get("album", [])
            if isinstance(albums, dict): albums = [albums]
            return [{"id": a.get("id", ""), "title": a.get("title", a.get("name", "Sin título")), "artist": a.get("artist", "Desconocido")} for a in albums]
    except Exception: return []

def fetch_artistas():
    url = generar_url_api("getArtists.view")
    if not url: return []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'NaviPort'})
        with urllib.request.urlopen(req, timeout=8) as res:
            data = json.loads(res.read().decode())
            indices = data.get("subsonic-response", {}).get("artists", {}).get("index", [])
            lista = []
            for i in indices:
                artistas = i.get("artist", [])
                if isinstance(artistas, dict): artistas = [artistas]
                for a in artistas: lista.append({"id": a.get("id", ""), "title": a.get("name", "Desconocido")})
            return lista
    except Exception: return []

def fetch_playlists():
    url = generar_url_api("getPlaylists.view")
    if not url: return []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'NaviPort'})
        with urllib.request.urlopen(req, timeout=8) as res:
            data = json.loads(res.read().decode())
            playlists = data.get("subsonic-response", {}).get("playlists", {}).get("playlist", [])
            if isinstance(playlists, dict): playlists = [playlists]
            return [{"id": p.get("id", ""), "title": p.get("name", "Sin título"), "count": p.get("songCount", 0)} for p in playlists]
    except Exception: return []

def fetch_canciones(tipo, id_item):
    endpoint = "getAlbum.view" if tipo == "album" else "getPlaylist.view"
    url = generar_url_api(endpoint, {"id": id_item})
    if not url: return []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'NaviPort'})
        with urllib.request.urlopen(req, timeout=8) as res:
            data = json.loads(res.read().decode())
            nodo = data.get("subsonic-response", {}).get(tipo, {})
            songs = nodo.get("song", []) if tipo == "album" else nodo.get("entry", [])
            if isinstance(songs, dict): songs = [songs]
            return [{"id": s.get("id", ""), "title": s.get("title", "Sin título"), "duration": int(s.get("duration", 0))} for s in songs]
    except Exception: return []

def fetch_albumes_artista(artist_id):
    url = generar_url_api("getArtist.view", {"id": artist_id})
    if not url: return []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'NaviPort'})
        with urllib.request.urlopen(req, timeout=8) as res:
            data = json.loads(res.read().decode())
            albums = data.get("subsonic-response", {}).get("artist", {}).get("album", [])
            if isinstance(albums, dict): albums = [albums]
            return [{"id": a.get("id", ""), "title": a.get("title", a.get("name", "Sin título")), "artist": a.get("artist", "Desconocido")} for a in albums]
    except Exception: return []

# --- HILOS DE TRABAJO (WORKERS) ---

def worker_carga_datos(funcion_fetch, args, target_state):
    global async_state
    async_state["status"] = "loading"
    try:
        resultado = funcion_fetch(*args)
        async_state["data"] = resultado
        async_state["target_state"] = target_state
        async_state["status"] = "done"
    except Exception as e:
        async_state["error"] = str(e)
        async_state["status"] = "error"

def iniciar_carga_background(funcion_fetch, args, target_state):
    t = threading.Thread(target=worker_carga_datos, args=(funcion_fetch, args, target_state), daemon=True)
    t.start()

def worker_descarga_caratula(album_id):
    url = generar_url_api("getCoverArt.view", {"id": album_id, "size": 300})
    if not url: return
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'NaviPort'})
        with urllib.request.urlopen(req, timeout=5) as res:
            img_bytes = res.read()
            cover_queue.append((album_id, img_bytes))
    except Exception: pass

def solicitar_caratula(album_id):
    if album_id in cover_cache: return
    t = threading.Thread(target=worker_descarga_caratula, args=(album_id,), daemon=True)
    t.start()

# --- UTILIDADES UI ---

def truncar_texto(texto, max_caracteres=32):
    return texto[:max_caracteres-3] + "..." if len(texto) > max_caracteres else texto

def animar_texto_largo(texto, ticks, max_caracteres=30):
    if len(texto) <= max_caracteres:
        return texto
    texto_con_espacio = texto + "        "
    desplazamiento = (ticks // 10) % len(texto_con_espacio)
    res = texto_con_espacio[desplazamiento:] + texto_con_espacio[:desplazamiento]
    return res[:max_caracteres]

def formatear_tiempo(segundos):
    return f"{int(segundos // 60):02d}:{int(segundos % 60):02d}"

# --- BUCLE PRINCIPAL ---

def main():
    global audio_process, is_audio_paused
    
    pygame.init()
    # Eliminado pygame.joystick.init()
    pygame.display.set_allow_screensaver(False)

    screen_info = pygame.display.Info()
    WIDTH, HEIGHT = screen_info.current_w, screen_info.current_h
    if WIDTH == 0 or HEIGHT == 0: WIDTH, HEIGHT = 640, 480
    screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.FULLSCREEN | pygame.SCALED if screen_info.current_w > 0 else 0)
    pygame.display.set_caption("NaviPort v0.2.1-fix")

    # Arrancar Hilo de Telemetría (Batería)
    t_telemetria = threading.Thread(target=worker_actualizar_telemetria, daemon=True)
    t_telemetria.start()

    # Colores UI
    BG_COLOR = (18, 18, 22)
    TEXT_COLOR = (220, 220, 225)
    TEXT_MUTED = (130, 130, 140)
    SELECT_COLOR = (255, 122, 0)
    PLAYING_COLOR = (0, 200, 100)
    PAUSE_COLOR = (200, 160, 0)
    BAR_BG_COLOR = (45, 45, 50)

    font = pygame.font.Font(None, int(HEIGHT * 0.05))
    font_small = pygame.font.Font(None, int(HEIGHT * 0.04))
    clock = pygame.time.Clock()

    # Pila de Navegación (Memoria de estado)
    nav_stack = []

    # Variables de Estado Actual
    estado_actual = STATE_MENU_PRINCIPAL
    lista_actual = MENU_ITEMS
    canciones_actuales = []
    
    index_seleccionado = 0
    scroll_offset = 0
    index_cancion_sonando = -1
    
    id_item_contexto = ""
    titulo_contexto = ""
    
    modo_aleatorio = False
    volumen_sistema = 70
    tiempo_total_segundos = 0
    
    cooldown_scroll_ms = 0
    tiempo_direccion_pulsada_ms = 0
    ticks_animacion = 0
    
    running = True

    while running:
        dt_ms = clock.tick(30)
        ticks_animacion += 1
        
        # 1. Procesar Cola de Imágenes
        while cover_queue:
            a_id, img_b = cover_queue.pop(0)
            try:
                surf = pygame.image.load(io.BytesIO(img_b))
                surf = pygame.transform.scale(surf, (int(HEIGHT * 0.4), int(HEIGHT * 0.4)))
                if len(cover_cache) >= MAX_CACHE_SIZE: cover_cache.pop(next(iter(cover_cache)))
                cover_cache[a_id] = surf
            except Exception: pass

        # 2. Control de Auto-Avance de MPV
        if audio_process and audio_process.poll() is not None:
            audio_process = None
            is_audio_paused = False
            
            if len(canciones_actuales) > 0 and index_cancion_sonando != -1:
                if modo_aleatorio: index_cancion_sonando = random.randint(0, len(canciones_actuales) - 1)
                else: index_cancion_sonando = (index_cancion_sonando + 1) % len(canciones_actuales)
                
                if estado_actual == STATE_TRACKS:
                    index_seleccionado = index_cancion_sonando
                    spacing_tmp = int(HEIGHT * 0.08)
                    max_items_visibles_tmp = (HEIGHT - int(HEIGHT * 0.35)) // spacing_tmp
                    if index_seleccionado < scroll_offset: scroll_offset = index_seleccionado
                    elif index_seleccionado >= scroll_offset + max_items_visibles_tmp: scroll_offset = index_seleccionado - max_items_visibles_tmp + 1
                    
                siguiente = canciones_actuales[index_cancion_sonando]
                tiempo_total_segundos = siguiente.get("duration", 0)
                reproducir_stream_sistema(generar_url_api("stream.view", {"id": siguiente["id"]}))

        musica_activa = audio_process and audio_process.poll() is None
        tiempo_actual_mpv = obtener_tiempo_mpv() if musica_activa else 0.0

        # 3. Transición de Carga Asíncrona
        if estado_actual == STATE_LOADING:
            if async_state["status"] == "done":
                lista_actual = async_state["data"]
                estado_actual = async_state["target_state"]
                index_seleccionado = 0
                scroll_offset = 0
            elif async_state["status"] == "error":
                lista_actual = [{"id": "", "title": "Error de conexión"}]
                estado_actual = async_state["target_state"]

        # 4. Eventos e Inputs
        mover_y, mover_x = 0, 0
        accion_aceptar, accion_volver, accion_pausa, accion_shuffle = False, False, False, False
        cambio_volumen = 0
        
        if cooldown_scroll_ms > 0: cooldown_scroll_ms -= dt_ms

        # Bloque seguro para evitar crashes por hardware
        try:
            for event in pygame.event.get():
                if event.type == pygame.QUIT: 
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE: running = True       # SELECT + START (Salir)
                    elif event.key == pygame.K_RETURN: accion_aceptar = True # Botón A
                    elif event.key == pygame.K_BACKSPACE: accion_volver = True # Botón B
                    elif event.key == pygame.K_SPACE: accion_pausa = True    # Botón X
                    elif event.key == pygame.K_y: accion_shuffle = True      # Botón Y
                    elif event.key == pygame.K_PAGEUP: cambio_volumen = 5    # Botón R1
                    elif event.key == pygame.K_PAGEDOWN: cambio_volumen = -5 # Botón L1
        except Exception:
            pass # Ignoramos eventos no mapeables o corrupciones del OS

        # Bloque seguro para teclas mantenidas
        try:
            keys = pygame.key.get_pressed()
            if keys[pygame.K_UP]: mover_y = -1
            elif keys[pygame.K_DOWN]: mover_y = 1
            if keys[pygame.K_LEFT]: mover_x = -1
            elif keys[pygame.K_RIGHT]: mover_x = 1
        except Exception:
            pass

        if cambio_volumen != 0:
            volumen_sistema = max(0, min(100, volumen_sistema + cambio_volumen))
            try: subprocess.Popen(["amixer", "set", "Master", f"{volumen_sistema}%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception: pass

        if accion_shuffle: modo_aleatorio = not modo_aleatorio
        if accion_pausa: alternar_pausa_sistema()

        # 5. Lógica de Scroll y Paginación
        spacing = int(HEIGHT * 0.10) if estado_actual in [STATE_ALBUMS, STATE_PLAYLISTS] else int(HEIGHT * 0.08)
        max_items_visibles = (HEIGHT - int(HEIGHT * 0.35)) // spacing
        
        if (mover_y != 0 or mover_x != 0) and estado_actual != STATE_LOADING:
            tiempo_direccion_pulsada_ms += dt_ms
            delay_req = 45 if tiempo_direccion_pulsada_ms > 500 else 160
            
            if cooldown_scroll_ms <= 0 and len(lista_actual) > 0:
                cooldown_scroll_ms = delay_req
                if mover_y != 0:
                    index_seleccionado = (index_seleccionado + mover_y) % len(lista_actual)
                    if index_seleccionado < scroll_offset: scroll_offset = index_seleccionado
                    elif index_seleccionado >= scroll_offset + max_items_visibles: scroll_offset = index_seleccionado - max_items_visibles + 1
                elif mover_x != 0:
                    if estado_actual == STATE_TRACKS and len(canciones_actuales) > 0:
                        cooldown_scroll_ms = 250
                        if modo_aleatorio and mover_x == 1: index_cancion_sonando = random.randint(0, len(canciones_actuales) - 1)
                        else: index_cancion_sonando = (index_cancion_sonando + mover_x) % len(canciones_actuales)
                        index_seleccionado = index_cancion_sonando
                        if index_seleccionado < scroll_offset: scroll_offset = index_seleccionado
                        elif index_seleccionado >= scroll_offset + max_items_visibles: scroll_offset = index_seleccionado - max_items_visibles + 1
                        
                        siguiente = canciones_actuales[index_cancion_sonando]
                        tiempo_total_segundos = siguiente.get("duration", 0)
                        reproducir_stream_sistema(generar_url_api("stream.view", {"id": siguiente["id"]}))
                    elif estado_actual in [STATE_ALBUMS, STATE_ARTISTS, STATE_PLAYLISTS]:
                        index_seleccionado = (index_seleccionado + (mover_x * 10)) % len(lista_actual)
                        if index_seleccionado < scroll_offset: scroll_offset = index_seleccionado
                        elif index_seleccionado >= scroll_offset + max_items_visibles: scroll_offset = max(0, index_seleccionado - max_items_visibles + 1)
        else:
            tiempo_direccion_pulsada_ms = 0

        # 6. Máquina de Acciones (Botón Aceptar y Guardado en Pila)
        if accion_aceptar and len(lista_actual) > 0 and estado_actual != STATE_LOADING:
            item = lista_actual[index_seleccionado]
            
            # Guardamos el estado actual antes de avanzar
            estado_saliente = {
                "state": estado_actual,
                "list": lista_actual,
                "index": index_seleccionado,
                "scroll": scroll_offset,
                "id_contexto": id_item_contexto,
                "titulo_contexto": titulo_contexto
            }
            
            if estado_actual == STATE_MENU_PRINCIPAL:
                nav_stack.append(estado_saliente)
                estado_actual = STATE_LOADING
                if item["action"] == "album_random": iniciar_carga_background(fetch_albumes, ("random",), STATE_ALBUMS)
                elif item["action"] == "album_recent": iniciar_carga_background(fetch_albumes, ("recent",), STATE_ALBUMS)
                elif item["action"] == "album_alpha": iniciar_carga_background(fetch_albumes, ("alphabeticalByName",), STATE_ALBUMS)
                elif item["action"] == "artists": iniciar_carga_background(fetch_artistas, (), STATE_ARTISTS)
                elif item["action"] == "playlists": iniciar_carga_background(fetch_playlists, (), STATE_PLAYLISTS)
                
            elif estado_actual in [STATE_ALBUMS, STATE_PLAYLISTS]:
                nav_stack.append(estado_saliente)
                tipo_req = "album" if estado_actual == STATE_ALBUMS else "playlist"
                estado_actual = STATE_LOADING
                id_item_contexto = item["id"]
                titulo_contexto = item.get("title", item.get("name", "Desconocido"))
                
                if tipo_req == "album": solicitar_caratula(id_item_contexto)
                iniciar_carga_background(fetch_canciones, (tipo_req, id_item_contexto), STATE_TRACKS)
                
            elif estado_actual == STATE_ARTISTS:
                nav_stack.append(estado_saliente)
                estado_actual = STATE_LOADING
                iniciar_carga_background(fetch_albumes_artista, (item["id"],), STATE_ALBUMS)
                
            elif estado_actual == STATE_TRACKS:
                canciones_actuales = lista_actual
                index_cancion_sonando = index_seleccionado
                tiempo_total_segundos = item.get("duration", 0)
                reproducir_stream_sistema(generar_url_api("stream.view", {"id": item["id"]}))

        # 7. Máquina de Acciones (Botón Volver con Recuperación de Pila)
        if accion_volver and estado_actual != STATE_LOADING:
            if nav_stack:
                estado_recuperado = nav_stack.pop()
                estado_actual = estado_recuperado["state"]
                lista_actual = estado_recuperado["list"]
                index_seleccionado = estado_recuperado["index"]
                scroll_offset = estado_recuperado["scroll"]
                id_item_contexto = estado_recuperado["id_contexto"]
                titulo_contexto = estado_recuperado["titulo_contexto"]

        # --- RENDERIZADO UI ---
        screen.fill(BG_COLOR)
        
        # HUD Superior (Reloj y Batería)
        hora_actual = datetime.now().strftime("%H:%M")
        txt_telemetria = font_small.render(f"BAT: {nivel_bateria} | {hora_actual}", True, TEXT_MUTED)
        screen.blit(txt_telemetria, (WIDTH - txt_telemetria.get_width() - int(WIDTH * 0.05), int(HEIGHT * 0.05)))

        # HUD Inferior
        hud_h = int(HEIGHT * 0.16)
        hud_y = HEIGHT - hud_h
        pygame.draw.rect(screen, (26, 26, 32), (0, hud_y, WIDTH, hud_h))
        
        vol_text = font_small.render(f"VOL: {volumen_sistema}%", True, TEXT_MUTED)
        screen.blit(vol_text, (int(WIDTH * 0.75), hud_y + int(hud_h * 0.12)))
        pygame.draw.rect(screen, BAR_BG_COLOR, (int(WIDTH * 0.75), hud_y + int(hud_h * 0.42), int(WIDTH * 0.2), 6))
        pygame.draw.rect(screen, SELECT_COLOR, (int(WIDTH * 0.75), hud_y + int(hud_h * 0.42), int(WIDTH * 0.2 * (volumen_sistema/100)), 6))

        str_shuf = "[ALEAT] " if modo_aleatorio else ""
        if musica_activa and index_cancion_sonando != -1 and index_cancion_sonando < len(canciones_actuales):
            c_hud = PAUSE_COLOR if is_audio_paused else PLAYING_COLOR
            e_txt = "PAUSA" if is_audio_paused else "PLAY"
            t_tit_raw = canciones_actuales[index_cancion_sonando]["title"]
            # Marquee animado para el título en el HUD
            t_tit_anim = animar_texto_largo(t_tit_raw, ticks_animacion, max_caracteres=25)
            s_time = f"{formatear_tiempo(tiempo_actual_mpv)} / {formatear_tiempo(tiempo_total_segundos)}"
            txt_hud = font_small.render(f"{str_shuf}{e_txt}: {t_tit_anim} | {s_time}", True, c_hud)
            
            b_x, b_y, b_w, b_h = int(WIDTH * 0.05), hud_y + int(hud_h * 0.68), int(WIDTH * 0.9), int(HEIGHT * 0.015)
            pygame.draw.rect(screen, BAR_BG_COLOR, (b_x, b_y, b_w, b_h))
            if tiempo_total_segundos > 0:
                progreso = min(max(tiempo_actual_mpv / tiempo_total_segundos, 0.0), 1.0)
                pygame.draw.rect(screen, c_hud, (b_x, b_y, int(b_w * progreso), b_h))
        else:
            txt_hud = font_small.render(f"{str_shuf}Sin reproducción", True, TEXT_MUTED)
        
        screen.blit(txt_hud, (int(WIDTH * 0.05), hud_y + int(hud_h * 0.12)))

        start_y = int(HEIGHT * 0.15)

        if estado_actual == STATE_LOADING:
            puntos = "." * ((ticks_animacion // 10) % 4)
            txt_load = font.render(f"Descargando datos{puntos}", True, SELECT_COLOR)
            screen.blit(txt_load, (int(WIDTH * 0.05), start_y))
            
        else:
            t_cabecera = f"NAVIPORT - {estado_actual}" if estado_actual != STATE_TRACKS else f"Pistas: {truncar_texto(titulo_contexto, 15)}"
            cab = font.render(t_cabecera, True, SELECT_COLOR)
            screen.blit(cab, (int(WIDTH * 0.05), int(HEIGHT * 0.05)))
            
            list_x = int(WIDTH * 0.05)
            if estado_actual == STATE_TRACKS:
                list_x = int(WIDTH * 0.45)
                if id_item_contexto in cover_cache:
                    screen.blit(cover_cache[id_item_contexto], (int(WIDTH * 0.05), start_y))
                else:
                    pygame.draw.rect(screen, (35, 35, 40), (int(WIDTH * 0.05), start_y, int(HEIGHT * 0.4), int(HEIGHT * 0.4)))

            items_visibles = lista_actual[scroll_offset:scroll_offset + max_items_visibles]
            for i_vis, item in enumerate(items_visibles):
                i_real = scroll_offset + i_vis
                p_y = start_y + (i_vis * spacing)
                
                es_sel = (i_real == index_seleccionado)
                c_txt = SELECT_COLOR if es_sel else TEXT_COLOR
                pref = "> " if es_sel else "  "
                
                if estado_actual == STATE_TRACKS and musica_activa and i_real == index_cancion_sonando:
                    c_txt = PLAYING_COLOR if not is_audio_paused else PAUSE_COLOR
                
                titulo_item_raw = item.get('title', item.get('name', ''))
                # Aplicamos Marquee solo si está seleccionado, si no, lo truncamos estático
                if es_sel: titulo_item = animar_texto_largo(titulo_item_raw, ticks_animacion, max_caracteres=28)
                else: titulo_item = truncar_texto(titulo_item_raw, 28)
                
                txt = font.render(f"{pref}{titulo_item}", True, c_txt)
                screen.blit(txt, (list_x, p_y))
                
                if estado_actual in [STATE_ALBUMS, STATE_PLAYLISTS]:
                    sub_y = p_y + int(HEIGHT * 0.045)
                    sub_x = list_x + int(WIDTH * 0.03)
                    
                    if estado_actual == STATE_ALBUMS and "artist" in item:
                        txt_sub = font_small.render(truncar_texto(item["artist"], 35), True, TEXT_MUTED)
                        screen.blit(txt_sub, (sub_x, sub_y))
                    elif estado_actual == STATE_PLAYLISTS and "count" in item:
                        txt_sub = font_small.render(f"{item['count']} canciones", True, TEXT_MUTED)
                        screen.blit(txt_sub, (sub_x, sub_y))

        pygame.display.flip()

    detener_audio_sistema()
    pygame.quit()

if __name__ == "__main__":
    try: main()
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        detener_audio_sistema()
        pygame.quit()
        sys.exit(1)