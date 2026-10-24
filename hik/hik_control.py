"""
hik_control.py - Pilotage des cameras Hikvision via ISAPI
Usage:
    python hik_control.py                           # Menu interactif
    python hik_control.py info                       # Infos camera
    python hik_control.py presets                    # Lister les presets
    python hik_control.py goto 1                     # Aller au preset 1
    python hik_control.py patrol start 1             # Lancer patrouille 1
    python hik_control.py capture                    # Capturer une image
    python hik_control.py move left                  # Mouvement PTZ
    python hik_control.py stop                       # Stopper le mouvement
    python hik_control.py daynight auto              # Mode jour/nuit
    python hik_control.py home goto                  # Aller a la home position
    python hik_control.py park set preset 1 30       # Park action: preset 1 apres 30s
    python hik_control.py smart list                 # Lister les smart events
    python hik_control.py smart show parking         # Config parking detection
    python hik_control.py smart enable parking       # Activer parking detection
    python hik_control.py caps                       # Capacites camera
    python hik_control.py privacy on                 # Activer masques vie privee
    python hik_control.py privacy-all off            # DEMASQUER TOUTES les cameras
    python hik_control.py privacy-all on             # MASQUER TOUTES les cameras
    python hik_control.py --camera 1 goto 3          # Utiliser la camera index 1
    python hik_control.py reboot                     # Redemarrer la camera
"""

import requests
import json
import sys
import os
import time
import getpass

try:
    import keyring
    HAS_KEYRING = True
except ImportError:
    HAS_KEYRING = False

################################################################################
# Configuration
################################################################################

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "hik_cameras.json")

# Couleurs console
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_RED = "\033[91m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_BLUE = "\033[94m"
C_CYAN = "\033[96m"


################################################################################
# Camera class : pilotes par marque dans hik/drivers/ (un module par marque).
# HikCamera reste importable d'ici (hik.hik_control.HikCamera).
################################################################################

try:
    from hik.drivers import HikCamera  # noqa: F401  (import depuis cockpit/)
except ImportError:
    from drivers import HikCamera  # noqa: F401  (python hik_control.py depuis hik/)


################################################################################
# Gestion des credentials (Windows Credential Manager)
################################################################################

KEYRING_SERVICE = "hik_control"
PRIVACY_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".hik_privacy_cache.json")


def credential_store(group_name, password):
    """Stocke un mot de passe dans Windows Credential Manager."""
    if not HAS_KEYRING:
        print_err("Module 'keyring' non installe. pip install keyring")
        return False
    keyring.set_password(KEYRING_SERVICE, group_name, password)
    return True


def credential_get(group_name):
    """Recupere un mot de passe depuis Windows Credential Manager."""
    if not HAS_KEYRING:
        return None
    return keyring.get_password(KEYRING_SERVICE, group_name)


def credential_delete(group_name):
    """Supprime un mot de passe du Credential Manager."""
    if not HAS_KEYRING:
        return False
    try:
        keyring.delete_password(KEYRING_SERVICE, group_name)
        return True
    except keyring.errors.PasswordDeleteError:
        return False


################################################################################
# Chargement de la configuration
################################################################################

def load_cameras():
    """Charge les cameras depuis hik_cameras.json avec resolution des credentials."""
    if not os.path.exists(CONFIG_FILE):
        print(f"{C_RED}Fichier de config introuvable: {CONFIG_FILE}{C_RESET}")
        print(f"Creez le fichier avec la structure attendue (voir hik_cameras.json)")
        sys.exit(1)

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    cred_groups = config.get("credential_groups", {})
    cameras = []

    for cam in config.get("cameras", []):
        # Resoudre le user et password
        group_name = cam.get("credential_group")
        user = cam.get("user", "admin")
        password = cam.get("password", "")

        if group_name and group_name in cred_groups:
            group = cred_groups[group_name]
            user = cam.get("user") or group.get("user", "admin")
            # Chercher le mot de passe dans le Credential Manager
            stored_pw = credential_get(group_name)
            if stored_pw:
                password = stored_pw
            elif not password:
                # Fallback : mot de passe en clair dans le JSON (ancien format)
                password = cam.get("password", "")
        elif password.startswith("@credential:"):
            # Format direct @credential:nom_du_credential
            cred_name = password[len("@credential:"):]
            stored_pw = credential_get(cred_name)
            if stored_pw:
                password = stored_pw
            else:
                password = ""

        if not password:
            print(f"{C_YELLOW}ATTENTION: Pas de mot de passe pour {cam.get('name', cam['ip'])}"
                  f" (groupe: {group_name}){C_RESET}")
            print(f"  Utilisez: python hik_control.py credential set {group_name}")

        cameras.append(HikCamera(
            name=cam.get("name", f"Camera {cam['ip']}"),
            ip=cam["ip"],
            port=cam.get("port", 80),
            user=user,
            password=password,
            channel=cam.get("channel", 1),
            protocol=cam.get("protocol", "http"),
            brand=cam.get("brand", "hikvision"),
        ))
    return cameras


def select_camera(cameras, index=None):
    """Selectionne une camera par index ou affiche la liste."""
    if index is not None and 0 <= index < len(cameras):
        return cameras[index]

    if len(cameras) == 1:
        return cameras[0]

    print(f"\n{C_CYAN}{C_BOLD}Cameras disponibles:{C_RESET}")
    for i, cam in enumerate(cameras):
        print(f"  {C_BOLD}[{i}]{C_RESET} {cam.name} ({cam.ip} - {cam.brand})")

    while True:
        try:
            choice = input(f"\n{C_CYAN}Choisir une camera [0-{len(cameras)-1}]: {C_RESET}")
            idx = int(choice.strip())
            if 0 <= idx < len(cameras):
                return cameras[idx]
        except (ValueError, KeyboardInterrupt):
            print()
            sys.exit(0)


################################################################################
# Affichage
################################################################################

def print_ok(msg):
    print(f"{C_GREEN}{C_BOLD}OK{C_RESET} {msg}")

def print_err(msg):
    print(f"{C_RED}{C_BOLD}ERREUR{C_RESET} {msg}")

def print_info(msg):
    print(f"{C_CYAN}{msg}{C_RESET}")


################################################################################
# Commandes CLI
################################################################################

def cmd_info(cam):
    """Affiche les infos de la camera."""
    info = cam.get_device_info()
    print(f"\n{C_CYAN}{C_BOLD}=== {cam.name} ({cam.ip}) ==={C_RESET}")
    for k, v in info.items():
        print(f"  {C_BOLD}{k:25s}{C_RESET} {v}")

    pos = cam.get_ptz_status()
    if pos:
        print(f"\n  {C_BOLD}{'Position PTZ':25s}{C_RESET} "
              f"Azimut={pos['azimuth']:.1f} Elev={pos['elevation']:.1f} Zoom={pos['zoom']:.1f}")


def cmd_presets(cam):
    """Liste les presets."""
    presets = cam.list_presets()
    print(f"\n{C_CYAN}{C_BOLD}Presets de {cam.name}:{C_RESET}")
    if not presets:
        print("  Aucun preset configure")
        return
    for p in presets:
        status = f"{C_GREEN}actif{C_RESET}" if p["enabled"] else f"{C_YELLOW}inactif{C_RESET}"
        print(f"  {C_BOLD}[{p['id']:3d}]{C_RESET} {p['name']:30s} {status}")


def cmd_goto(cam, preset_id):
    """Va a un preset."""
    if cam.goto_preset(preset_id):
        print_ok(f"Deplacement vers preset {preset_id}")
    else:
        print_err(f"Impossible d'aller au preset {preset_id}")


def cmd_patrol(cam, action, patrol_id):
    """Lance ou stoppe une patrouille."""
    if action == "start":
        if cam.start_patrol(patrol_id):
            print_ok(f"Patrouille {patrol_id} lancee")
        else:
            print_err(f"Impossible de lancer la patrouille {patrol_id}")
    elif action == "stop":
        if cam.stop_patrol(patrol_id):
            print_ok(f"Patrouille {patrol_id} stoppee")
        else:
            print_err(f"Impossible de stopper la patrouille {patrol_id}")
    elif action == "list":
        patrols = cam.list_patrols()
        print(f"\n{C_CYAN}{C_BOLD}Patrouilles de {cam.name}:{C_RESET}")
        if not patrols:
            print("  Aucune patrouille configuree")
        for p in patrols:
            status = f"{C_GREEN}actif{C_RESET}" if p["enabled"] else f"{C_YELLOW}inactif{C_RESET}"
            print(f"  {C_BOLD}[{p['id']:3d}]{C_RESET} {p['name']:30s} {status}")


def cmd_capture(cam):
    """Capture une image."""
    path = cam.capture_image()
    print_ok(f"Image capturee: {path}")


def cmd_move(cam, direction, speed=50, duration=0):
    """Mouvement PTZ."""
    moves = {
        "left":     cam.move_left,
        "right":    cam.move_right,
        "up":       cam.move_up,
        "down":     cam.move_down,
        "zoomin":   cam.zoom_in,
        "zoomout":  cam.zoom_out,
    }

    fn = moves.get(direction.lower())
    if not fn:
        print_err(f"Direction inconnue: {direction}")
        print(f"  Directions: {', '.join(moves.keys())}")
        return

    if fn(speed):
        print_ok(f"Mouvement {direction} (vitesse {speed})")
        if duration > 0:
            time.sleep(duration)
            cam.stop_move()
            print_ok(f"Mouvement arrete apres {duration}s")
    else:
        print_err(f"Impossible de bouger en {direction}")


def cmd_stop(cam):
    """Stoppe le mouvement."""
    if cam.stop_move():
        print_ok("Mouvement stoppe")
    else:
        print_err("Impossible de stopper le mouvement")


def cmd_capabilities(cam):
    """Affiche les capacites de la camera."""
    print(f"\n{C_CYAN}{C_BOLD}=== Capacites de {cam.name} ==={C_RESET}")

    # Smart capabilities
    print(f"\n  {C_BOLD}Smart/VCA:{C_RESET}")
    smart_caps = cam.get_smart_capabilities()
    if smart_caps:
        for cap in smart_caps:
            print(f"    - {cap}")
    else:
        print(f"    {C_YELLOW}Non disponible{C_RESET}")

    # Event capabilities
    print(f"\n  {C_BOLD}Evenements:{C_RESET}")
    event_caps = cam.get_event_capabilities()
    if event_caps:
        for cap in event_caps:
            print(f"    - {cap}")
    else:
        print(f"    {C_YELLOW}Non disponible{C_RESET}")

    # PTZ capabilities
    print(f"\n  {C_BOLD}PTZ:{C_RESET}")
    ptz_caps = cam.get_ptz_capabilities()
    if ptz_caps:
        for k, v in ptz_caps.items():
            print(f"    {k:30s} {v}")
    else:
        print(f"    {C_YELLOW}Non disponible{C_RESET}")


def cmd_smart_events(cam, args):
    """Gestion des smart events."""
    if not args or args[0] == "list":
        # Lister le status de tous les smart events
        events = cam.list_smart_events_status()
        print(f"\n{C_CYAN}{C_BOLD}Smart Events de {cam.name}:{C_RESET}")
        print(f"  {'Evenement':20s} {'Endpoint':30s} {'Status':10s}")
        print(f"  {'-'*20} {'-'*30} {'-'*10}")
        for e in events:
            if e["available"]:
                if e["enabled"] == "true":
                    status = f"{C_GREEN}ACTIF{C_RESET}"
                elif e["enabled"] == "false":
                    status = f"{C_YELLOW}INACTIF{C_RESET}"
                else:
                    status = f"{C_BLUE}?{C_RESET}"
                print(f"  {e['key']:20s} {e['endpoint']:30s} {status}")
            else:
                print(f"  {e['key']:20s} {e['endpoint']:30s} {C_RED}NON DISPO{C_RESET}")
        return

    action = args[0].lower()

    if action == "show" and len(args) >= 2:
        event_key = args[1].lower()
        config = cam.get_smart_event_config(event_key)
        if config:
            print(f"\n{C_CYAN}{C_BOLD}Config {event_key}:{C_RESET}")
            print(json.dumps(config, indent=2, ensure_ascii=False))
        else:
            print_err(f"Evenement '{event_key}' non disponible ou non trouve")
            print(f"  Types: {', '.join(HikCamera.SMART_EVENT_ENDPOINTS.keys())}")
        return

    if action in ("enable", "disable") and len(args) >= 2:
        event_key = args[1].lower()
        enabled = action == "enable"
        if cam.set_smart_event_enabled(event_key, enabled):
            print_ok(f"{event_key} {'active' if enabled else 'desactive'}")
        else:
            print_err(f"Impossible de modifier {event_key}")
            print(f"  Types: {', '.join(HikCamera.SMART_EVENT_ENDPOINTS.keys())}")
        return

    print_err(f"Sous-commande inconnue: {action}")
    print(f"  smart list                    Lister le status des smart events")
    print(f"  smart show <type>             Afficher la config d'un event")
    print(f"  smart enable <type>           Activer un event")
    print(f"  smart disable <type>          Desactiver un event")
    print(f"  Types: {', '.join(HikCamera.SMART_EVENT_ENDPOINTS.keys())}")


def cmd_daynight(cam, args):
    """Gestion du mode jour/nuit."""
    if not args:
        mode = cam.get_daynight_mode()
        color = {
            "day": C_YELLOW, "night": C_BLUE, "auto": C_GREEN,
        }.get(mode, C_CYAN)
        print(f"  Mode actuel: {color}{C_BOLD}{mode}{C_RESET}")
        return

    mode = args[0].lower()
    if mode not in ("day", "night", "auto"):
        print_err(f"Mode inconnu: {mode}")
        print(f"  Modes: day, night, auto")
        return

    if cam.set_daynight_mode(mode):
        print_ok(f"Mode jour/nuit: {mode}")
    else:
        print_err(f"Impossible de changer le mode")


def cmd_home(cam, args):
    """Gestion de la home position."""
    if not args:
        pos = cam.get_home_position()
        if pos:
            print(f"  Home: Azimut={pos['azimuth']:.1f} Elev={pos['elevation']:.1f} Zoom={pos['zoom']:.1f}")
        else:
            print(f"  {C_YELLOW}Home position non definie ou non supportee{C_RESET}")
        return

    action = args[0].lower()
    if action == "set":
        if cam.set_home_position():
            print_ok("Position actuelle definie comme home")
        else:
            print_err("Impossible de definir la home position")
    elif action in ("goto", "go"):
        if cam.goto_home():
            print_ok("Deplacement vers home position")
        else:
            print_err("Impossible d'aller a la home position")
    else:
        print_err(f"Sous-commande inconnue: {action}")
        print(f"  home            Voir la home position")
        print(f"  home set        Definir la position actuelle comme home")
        print(f"  home goto       Aller a la home position")


def cmd_privacy(cam, args):
    """Gestion des masques de vie privee."""
    if not args:
        pm = cam.get_privacy_masks()
        if pm is None:
            print_err("Privacy mask non supporte sur cette camera")
            return
        global_status = f"{C_GREEN}ACTIF{C_RESET}" if pm["enabled"] else f"{C_RED}DESACTIVE{C_RESET}"
        print(f"\n  {C_BOLD}Privacy Masks:{C_RESET} {global_status}")
        if pm["masks"]:
            for m in pm["masks"]:
                ms = f"{C_GREEN}actif{C_RESET}" if m["enabled"] else f"{C_YELLOW}inactif{C_RESET}"
                print(f"    [{m['id']}] {m['name']:20s} {ms}")
        else:
            print(f"    Aucun masque configure")
        return

    action = args[0].lower()
    if action == "on":
        if cam.set_privacy_masks_enabled(True):
            print_ok("Masques de vie privee ACTIVES")
        else:
            print_err("Impossible d'activer les masques")
    elif action == "off":
        if cam.set_privacy_masks_enabled(False):
            print_ok("Masques de vie privee DESACTIVES")
        else:
            print_err("Impossible de desactiver les masques")
    else:
        print_err(f"Sous-commande inconnue: {action}")
        print(f"  privacy          Voir le status des masques")
        print(f"  privacy on       Activer les masques")
        print(f"  privacy off      Desactiver les masques (demasquage)")


def _load_privacy_cache():
    """Charge le cache des cameras dont le privacy ne fonctionne pas."""
    if os.path.exists(PRIVACY_CACHE_FILE):
        try:
            with open(PRIVACY_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_privacy_cache(cache):
    """Sauvegarde le cache."""
    with open(PRIVACY_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def _progress_bar(current, total, success, fail, skip, width=30):
    """Genere une barre de progression coloree."""
    pct = current / total if total > 0 else 0
    filled = int(width * pct)
    bar_ok = min(filled, int(width * success / total)) if total > 0 else 0
    bar_fail = min(filled - bar_ok, int(width * fail / total)) if total > 0 else 0
    bar_skip = filled - bar_ok - bar_fail
    bar_empty = width - filled

    bar = (
        f"{C_GREEN}{'█' * bar_ok}{C_RESET}"
        f"{C_RED}{'█' * bar_fail}{C_RESET}"
        f"{C_YELLOW}{'█' * bar_skip}{C_RESET}"
        f"{'░' * bar_empty}"
    )
    return f"  {bar} {current}/{total} ({pct*100:.0f}%) | {C_GREEN}{success} OK{C_RESET} {C_RED}{fail} FAIL{C_RESET} {C_YELLOW}{skip} SKIP{C_RESET}"


def cmd_privacy_all(cameras, enable, force=False):
    """Active ou desactive les privacy masks sur TOUTES les cameras."""
    action = "ACTIVATION" if enable else "DEMASQUAGE"
    total = len(cameras)
    cache = _load_privacy_cache() if not force else {}

    cached_skip = sum(1 for cam in cameras if cam.ip in cache and cam.brand not in ("bosch", "vivotek", "generic"))
    print(f"\n{C_CYAN}{C_BOLD}{'='*70}")
    print(f"  {action} des masques sur {total} camera(s)")
    if cached_skip and not force:
        print(f"  ({cached_skip} cameras ignorees d'apres le cache precedent)")
        print(f"  Utilisez {C_BOLD}privacy-all {'on' if enable else 'off'} --force{C_RESET}{C_CYAN} pour tout retester")
    print(f"{'='*70}{C_RESET}\n")

    success = 0
    fail = 0
    skip = 0
    results = []

    for i, cam in enumerate(cameras, 1):
        label = f"{cam.name[:35]:35s} {cam.ip:15s} {cam.brand:10s}"

        # Barre de progression
        print(f"\r{_progress_bar(i, total, success, fail, skip)}  {C_CYAN}{cam.name[:30]}{C_RESET}    ", end="", flush=True)

        # Marques sans support privacy API
        if cam.brand in ("bosch", "vivotek", "generic"):
            skip += 1
            results.append(("SKIP", label, "pas de support API"))
            continue

        # Cache : skip si deja en echec "fonction non disponible"
        if not force and cam.ip in cache and cache[cam.ip].get("reason") == "no_support":
            skip += 1
            results.append(("CACHE", label, cache[cam.ip].get("detail", "cache")))
            continue

        try:
            result = cam.set_privacy_masks_enabled(enable)
            if result:
                success += 1
                results.append(("OK", label, ""))
                # Retirer du cache si c'etait en echec avant
                cache.pop(cam.ip, None)
            else:
                fail += 1
                results.append(("FAIL", label, "fonction non disponible"))
                cache[cam.ip] = {"reason": "no_support", "detail": "fonction non disponible", "name": cam.name, "brand": cam.brand}
        except requests.exceptions.ConnectTimeout:
            fail += 1
            results.append(("FAIL", label, "timeout connexion"))
            # Pas de cache pour les timeouts (probleme reseau temporaire)
        except requests.exceptions.ReadTimeout:
            fail += 1
            results.append(("FAIL", label, "timeout lecture"))
        except requests.exceptions.ConnectionError:
            fail += 1
            results.append(("FAIL", label, "injoignable"))
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            if code == 401:
                reason = "auth refusee"
            elif code == 403:
                reason = "acces interdit"
            else:
                reason = f"HTTP {code}"
            fail += 1
            results.append(("FAIL", label, reason))
            # Cache si c'est un 403 (pas les droits = permanent)
            if code == 403:
                cache[cam.ip] = {"reason": "no_support", "detail": reason, "name": cam.name, "brand": cam.brand}
        except Exception as e:
            fail += 1
            results.append(("FAIL", label, str(e)[:50]))

    # Sauvegarder le cache
    _save_privacy_cache(cache)

    # Barre finale
    print(f"\r{_progress_bar(total, total, success, fail, skip)}{'':40s}")

    # Recap des erreurs
    fails = [r for r in results if r[0] == "FAIL"]
    cached = [r for r in results if r[0] == "CACHE"]
    brand_skips = [r for r in results if r[0] == "SKIP"]

    if fails:
        print(f"\n  {C_RED}{C_BOLD}Echecs ({len(fails)}):{C_RESET}")
        for _, label, reason in fails:
            print(f"    {C_RED}x{C_RESET} {label} - {reason}")

    if cached:
        print(f"\n  {C_YELLOW}{C_BOLD}Ignores par cache ({len(cached)}):{C_RESET} (--force pour retester)")

    if brand_skips:
        print(f"  {C_YELLOW}Ignores par marque: {len(brand_skips)} cameras (bosch/vivotek/generic){C_RESET}")

    print(f"\n{C_CYAN}{C_BOLD}{'='*70}{C_RESET}")
    print(f"  {C_BOLD}Resultat:{C_RESET}  {C_GREEN}{C_BOLD}{success} OK{C_RESET}  /  {C_RED}{C_BOLD}{fail} echec(s){C_RESET}  /  {C_YELLOW}{C_BOLD}{skip} ignore(s){C_RESET}  (total {total})")
    print(f"{C_CYAN}{C_BOLD}{'='*70}{C_RESET}")


def cmd_credential(args, config_path=None):
    """Gestion des credentials dans Windows Credential Manager."""
    if not HAS_KEYRING:
        print_err("Module 'keyring' non installe.")
        print(f"  Installez-le avec: pip install keyring")
        return

    if not args:
        print(f"\n{C_CYAN}{C_BOLD}Gestion des credentials (Windows Credential Manager){C_RESET}")
        print(f"  {C_BOLD}credential list{C_RESET}              Lister les groupes et leur status")
        print(f"  {C_BOLD}credential set <groupe>{C_RESET}      Stocker un mot de passe")
        print(f"  {C_BOLD}credential del <groupe>{C_RESET}      Supprimer un mot de passe")
        print(f"  {C_BOLD}credential test <groupe>{C_RESET}     Verifier qu'un mot de passe est stocke")
        return

    action = args[0].lower()

    if action == "list":
        # Lire les groupes depuis le JSON
        if config_path and os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            groups = config.get("credential_groups", {})
            print(f"\n{C_CYAN}{C_BOLD}Groupes de credentials:{C_RESET}")
            print(f"  {'Groupe':25s} {'User':15s} {'Password':15s} {'Cameras':s}")
            print(f"  {'-'*25} {'-'*15} {'-'*15} {'-'*20}")

            # Compter les cameras par groupe
            cam_count = {}
            for cam in config.get("cameras", []):
                g = cam.get("credential_group", "")
                if g:
                    cam_count[g] = cam_count.get(g, 0) + 1

            for name, group in groups.items():
                user = group.get("user", "admin")
                stored = credential_get(name)
                if stored:
                    pw_status = f"{C_GREEN}STOCKE{C_RESET}"
                else:
                    pw_status = f"{C_RED}MANQUANT{C_RESET}"
                count = cam_count.get(name, 0)
                print(f"  {name:25s} {user:15s} {pw_status:15s} {count} camera(s)")
        return

    if action == "set" and len(args) >= 2:
        group_name = args[1]
        print(f"  Stockage du mot de passe pour le groupe: {C_BOLD}{group_name}{C_RESET}")
        password = getpass.getpass(f"  Mot de passe: ")
        if not password:
            print_err("Mot de passe vide, abandon")
            return
        password2 = getpass.getpass(f"  Confirmer: ")
        if password != password2:
            print_err("Les mots de passe ne correspondent pas")
            return
        if credential_store(group_name, password):
            print_ok(f"Mot de passe stocke pour '{group_name}' dans Windows Credential Manager")
        else:
            print_err("Impossible de stocker le mot de passe")
        return

    if action == "del" and len(args) >= 2:
        group_name = args[1]
        if credential_delete(group_name):
            print_ok(f"Credential '{group_name}' supprime")
        else:
            print_err(f"Credential '{group_name}' non trouve ou impossible a supprimer")
        return

    if action == "test" and len(args) >= 2:
        group_name = args[1]
        stored = credential_get(group_name)
        if stored:
            masked = stored[0] + "*" * (len(stored) - 2) + stored[-1] if len(stored) > 2 else "**"
            print_ok(f"Credential '{group_name}' existe: {masked}")
        else:
            print_err(f"Credential '{group_name}' NON TROUVE")
        return

    print_err(f"Sous-commande inconnue: {action}")
    cmd_credential([], config_path)


def cmd_park(cam, args):
    """Gestion du park action."""
    if not args or args[0] == "show":
        pa = cam.get_park_action()
        if pa:
            status = f"{C_GREEN}ACTIF{C_RESET}" if pa["enabled"] else f"{C_YELLOW}INACTIF{C_RESET}"
            print(f"\n  {C_BOLD}Park Action:{C_RESET}")
            print(f"    Status      : {status}")
            print(f"    Delai       : {pa['park_time']}s d'inactivite")
            print(f"    Action      : {pa['action_type']}")
            print(f"    ID          : {pa['action_id']}")
        else:
            print(f"  {C_YELLOW}Park action non supporte{C_RESET}")
        return

    action = args[0].lower()

    if action == "off":
        if cam.set_park_action(enabled=False):
            print_ok("Park action desactive")
        else:
            print_err("Impossible de desactiver le park action")
        return

    if action == "set" and len(args) >= 4:
        # park set <type> <id> <delai>
        action_type = args[1]  # preset, patrol, pattern, scan, homePosition
        action_id = int(args[2])
        park_time = int(args[3])
        if cam.set_park_action(True, park_time, action_type, action_id):
            print_ok(f"Park action: {action_type} #{action_id} apres {park_time}s")
        else:
            print_err("Impossible de configurer le park action")
        return

    print_err(f"Sous-commande inconnue: {action}")
    print(f"  park                              Voir la config")
    print(f"  park off                          Desactiver")
    print(f"  park set <type> <id> <delai_s>    Configurer")
    print(f"    Types: preset, patrol, pattern, scan, homePosition")
    print(f"    Ex: park set preset 1 30        Retour preset 1 apres 30s")


################################################################################
# Menu interactif
################################################################################

def interactive_menu(cam, all_cameras=None):
    """Menu interactif pour controler la camera."""
    print(f"""
{C_CYAN}{C_BOLD}{'='*60}
  HIK CONTROL - Pilotage camera Hikvision
  Camera: {cam.name} ({cam.ip} - {cam.brand})
{'='*60}{C_RESET}

  Commandes disponibles:
    {C_BOLD}info{C_RESET}              Infos de la camera
    {C_BOLD}presets{C_RESET}           Lister les presets
    {C_BOLD}goto <N>{C_RESET}          Aller au preset N
    {C_BOLD}set <N> [nom]{C_RESET}     Sauver la position actuelle comme preset N
    {C_BOLD}del <N>{C_RESET}           Supprimer le preset N
    {C_BOLD}patrol list{C_RESET}       Lister les patrouilles
    {C_BOLD}patrol start <N>{C_RESET}  Lancer la patrouille N
    {C_BOLD}patrol stop <N>{C_RESET}   Stopper la patrouille N
    {C_BOLD}move <dir> [vit]{C_RESET}  Mouvement (left/right/up/down/zoomin/zoomout)
    {C_BOLD}stop{C_RESET}              Stopper le mouvement
    {C_BOLD}pos <az> <el> <z>{C_RESET} Position absolue (azimut elevation zoom)
    {C_BOLD}capture{C_RESET}           Capturer une image
    {C_BOLD}wiper{C_RESET}             Activer l'essuie-glace
    {C_BOLD}light on/off{C_RESET}      Allumer/eteindre la lumiere
    {C_BOLD}lensinit{C_RESET}          Reinitialiser l'objectif
    {C_BOLD}autofocus{C_RESET}         Declencher l'autofocus

    {C_BOLD}daynight [mode]{C_RESET}   Mode jour/nuit (day/night/auto)
    {C_BOLD}home [set|goto]{C_RESET}   Home position (voir/definir/aller)
    {C_BOLD}park [show|off]{C_RESET}   Park action (retour auto)
    {C_BOLD}park set <t> <id> <s>{C_RESET} Configurer park (type id delai)

    {C_BOLD}smart [list]{C_RESET}      Lister les smart events
    {C_BOLD}smart show <type>{C_RESET} Config d'un smart event
    {C_BOLD}smart enable <type>{C_RESET}  Activer un smart event
    {C_BOLD}smart disable <type>{C_RESET} Desactiver un smart event
    {C_BOLD}caps{C_RESET}              Capacites de la camera

    {C_BOLD}privacy{C_RESET}           Voir les masques de vie privee
    {C_BOLD}privacy on/off{C_RESET}    Activer/desactiver les masques
    {C_BOLD}privacy-all on/off{C_RESET}       Masquer/demasquer TOUTES les cameras
    {C_BOLD}privacy-all off --force{C_RESET}  Idem mais reteste les cameras en cache

    {C_BOLD}reboot{C_RESET}            Redemarrer la camera
    {C_BOLD}quit{C_RESET}              Quitter
""")

    while True:
        try:
            line = input(f"{C_CYAN}hik>{C_RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if not line:
            continue

        parts = line.split()
        cmd = parts[0].lower()

        try:
            if cmd in ("quit", "exit", "q"):
                break

            elif cmd == "info":
                cmd_info(cam)

            elif cmd == "presets":
                cmd_presets(cam)

            elif cmd == "goto" and len(parts) >= 2:
                cmd_goto(cam, int(parts[1]))

            elif cmd == "set" and len(parts) >= 2:
                name = " ".join(parts[2:]) if len(parts) > 2 else ""
                if cam.set_preset(int(parts[1]), name):
                    print_ok(f"Preset {parts[1]} sauvegarde")
                else:
                    print_err(f"Impossible de sauvegarder le preset {parts[1]}")

            elif cmd == "del" and len(parts) >= 2:
                if cam.delete_preset(int(parts[1])):
                    print_ok(f"Preset {parts[1]} supprime")
                else:
                    print_err(f"Impossible de supprimer le preset {parts[1]}")

            elif cmd == "patrol" and len(parts) >= 2:
                action = parts[1].lower()
                patrol_id = int(parts[2]) if len(parts) >= 3 else 1
                cmd_patrol(cam, action, patrol_id)

            elif cmd == "move" and len(parts) >= 2:
                speed = int(parts[2]) if len(parts) >= 3 else 50
                duration = float(parts[3]) if len(parts) >= 4 else 0
                cmd_move(cam, parts[1], speed, duration)

            elif cmd == "stop":
                cmd_stop(cam)

            elif cmd == "pos" and len(parts) >= 4:
                az = int(float(parts[1]) * 10)
                el = int(float(parts[2]) * 10)
                zm = int(float(parts[3]) * 10)
                if cam.goto_position(az, el, zm):
                    print_ok(f"Position: azimut={parts[1]} elevation={parts[2]} zoom={parts[3]}")
                else:
                    print_err("Impossible de bouger a cette position")

            elif cmd == "capture":
                cmd_capture(cam)

            elif cmd == "wiper":
                if cam.wiper():
                    print_ok("Wiper active")
                else:
                    print_err("Wiper non disponible")

            elif cmd == "light" and len(parts) >= 2:
                if parts[1].lower() == "on":
                    if cam.light_on():
                        print_ok("Lumiere allumee")
                    else:
                        print_err("Lumiere non disponible")
                elif parts[1].lower() == "off":
                    if cam.light_off():
                        print_ok("Lumiere eteinte")
                    else:
                        print_err("Lumiere non disponible")

            elif cmd == "lensinit":
                if cam.lens_init():
                    print_ok("Objectif en cours de reinitialisation...")
                else:
                    print_err("Lens init non disponible sur cette camera")

            elif cmd == "autofocus":
                if cam.autofocus():
                    print_ok("Autofocus declenche")
                else:
                    print_err("Autofocus non disponible")

            elif cmd == "daynight":
                cmd_daynight(cam, parts[1:])

            elif cmd == "home":
                cmd_home(cam, parts[1:])

            elif cmd == "park":
                cmd_park(cam, parts[1:])

            elif cmd == "smart":
                cmd_smart_events(cam, parts[1:])

            elif cmd == "caps":
                cmd_capabilities(cam)

            elif cmd == "privacy-all" and len(parts) >= 2 and all_cameras:
                enable = parts[1].lower() in ("on", "enable", "activer")
                force = "--force" in parts
                cmd_privacy_all(all_cameras, enable, force=force)

            elif cmd == "privacy":
                cmd_privacy(cam, parts[1:])

            elif cmd == "reboot":
                confirm = input(f"{C_RED}Confirmer le redemarrage de {cam.name}? (oui/non): {C_RESET}")
                if confirm.strip().lower() in ("oui", "o", "yes", "y"):
                    if cam.reboot():
                        print_ok("Camera en cours de redemarrage...")
                    else:
                        print_err("Impossible de redemarrer")

            else:
                print_err(f"Commande inconnue: {line}")

        except requests.exceptions.ConnectionError:
            print_err(f"Impossible de se connecter a {cam.ip}:{cam.port}")
        except requests.exceptions.HTTPError as e:
            print_err(f"Erreur HTTP: {e}")
        except requests.exceptions.Timeout:
            print_err("Timeout de connexion")
        except Exception as e:
            print_err(f"{e}")


################################################################################
# Main
################################################################################

def main():
    cameras = load_cameras()

    # Parser les arguments
    args = sys.argv[1:]
    cam_index = None

    # Option --camera N
    if "--camera" in args:
        idx = args.index("--camera")
        cam_index = int(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    # Commandes credential (avant la selection de camera)
    if args and args[0].lower() == "credential":
        cmd_credential(args[1:], config_path=CONFIG_FILE)
        return

    # Commandes batch (toutes les cameras)
    if args and args[0].lower() == "privacy-all" and len(args) >= 2:
        enable = args[1].lower() in ("on", "enable", "activer")
        force = "--force" in args
        cmd_privacy_all(cameras, enable, force=force)
        return

    cam = select_camera(cameras, cam_index)
    print_info(f"Camera selectionnee: {cam.name} ({cam.ip} - {cam.brand})")

    if not args:
        # Mode interactif
        interactive_menu(cam, all_cameras=cameras)
        return

    cmd = args[0].lower()

    try:
        if cmd == "info":
            cmd_info(cam)

        elif cmd == "presets":
            cmd_presets(cam)

        elif cmd == "goto" and len(args) >= 2:
            cmd_goto(cam, int(args[1]))

        elif cmd == "patrol" and len(args) >= 2:
            action = args[1].lower()
            patrol_id = int(args[2]) if len(args) >= 3 else 1
            cmd_patrol(cam, action, patrol_id)

        elif cmd == "capture":
            cmd_capture(cam)

        elif cmd == "move" and len(args) >= 2:
            speed = int(args[2]) if len(args) >= 3 else 50
            duration = float(args[3]) if len(args) >= 4 else 0
            cmd_move(cam, args[1], speed, duration)

        elif cmd == "stop":
            cmd_stop(cam)

        elif cmd == "lensinit":
            if cam.lens_init():
                print_ok("Objectif en cours de reinitialisation...")
            else:
                print_err("Lens init non disponible sur cette camera")

        elif cmd == "autofocus":
            if cam.autofocus():
                print_ok("Autofocus declenche")
            else:
                print_err("Autofocus non disponible")

        elif cmd == "daynight":
            cmd_daynight(cam, args[1:])

        elif cmd == "home":
            cmd_home(cam, args[1:])

        elif cmd == "park":
            cmd_park(cam, args[1:])

        elif cmd == "smart":
            cmd_smart_events(cam, args[1:])

        elif cmd == "caps":
            cmd_capabilities(cam)

        elif cmd == "privacy":
            cmd_privacy(cam, args[1:])

        elif cmd == "reboot":
            if cam.reboot():
                print_ok("Camera en cours de redemarrage...")
            else:
                print_err("Impossible de redemarrer")

        else:
            print_err(f"Commande inconnue: {cmd}")
            print(f"Usage: python hik_control.py [--camera N] <commande> [args]")
            print(f"Commandes: info, presets, goto, patrol, capture, move, stop,")
            print(f"           daynight, home, park, smart, caps, privacy, reboot")

    except requests.exceptions.ConnectionError:
        print_err(f"Impossible de se connecter a {cam.ip}:{cam.port}")
        print(f"  Verifiez l'IP, le port et la connectivite reseau")
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            print_err("Authentification refusee - verifiez login/mot de passe")
        else:
            print_err(f"Erreur HTTP {e.response.status_code}: {e}")
    except requests.exceptions.Timeout:
        print_err("Timeout de connexion")


if __name__ == "__main__":
    main()
