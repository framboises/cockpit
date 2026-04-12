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
from requests.auth import HTTPDigestAuth
import xml.etree.ElementTree as ET
import json
import sys
import os
import re
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
# Camera class
################################################################################

class HikCamera:
    """Classe de controle d'une camera multi-marques."""

    def __init__(self, name, ip, port, user, password, channel=1, protocol="http", brand="hikvision"):
        self.name = name
        self.ip = ip
        self.port = port
        self.user = user
        self.password = password
        self.channel = channel
        self.brand = brand
        self.base_url = f"{protocol}://{ip}:{port}"
        self.auth = HTTPDigestAuth(user, password)
        self.timeout = 10

    def _url(self, path):
        return f"{self.base_url}{path}"

    def _get(self, path):
        r = requests.get(self._url(path), auth=self.auth, timeout=self.timeout)
        r.raise_for_status()
        return r

    def _put(self, path, data=None, content_type="application/xml"):
        headers = {"Content-Type": content_type} if data else {}
        r = requests.put(
            self._url(path), auth=self.auth, data=data,
            headers=headers, timeout=self.timeout,
        )
        r.raise_for_status()
        return r

    def _delete(self, path):
        r = requests.delete(self._url(path), auth=self.auth, timeout=self.timeout)
        r.raise_for_status()
        return r

    def _strip_ns(self, xml_text):
        return re.sub(r'xmlns="[^"]*"', '', xml_text)

    ############################################################################
    # Infos camera
    ############################################################################

    def get_device_info(self):
        """Recupere les informations de la camera (multi-marques)."""
        dispatch = {
            "hikvision": self._info_hik,
            "dahua":     self._info_dahua,
            "bosch":     self._info_bosch,
            "hanwha":    self._info_hanwha,
            "axis":      self._info_axis,
            "uniview":   self._info_hik,
        }
        fn = dispatch.get(self.brand, self._info_hik)
        try:
            info = fn()
            info["_brand"] = self.brand
            return info
        except Exception as e:
            return {"_brand": self.brand, "_error": str(e)}

    def _info_hik(self):
        r = self._get("/ISAPI/System/deviceInfo")
        xml = self._strip_ns(r.text)
        root = ET.fromstring(xml)
        info = {}
        for child in root:
            tag = re.sub(r'\{[^}]*\}', '', child.tag)
            if child.text:
                info[tag] = child.text.strip()
        return info

    def _info_dahua(self):
        r = self._get("/cgi-bin/magicBox.cgi?action=getSystemInfo")
        info = {}
        for line in r.text.strip().split("\n"):
            parts = line.strip().split("=", 1)
            if len(parts) == 2:
                info[parts[0].strip()] = parts[1].strip()
        return info

    def _info_bosch(self):
        info = {}
        # Utiliser ONVIF GetDeviceInformation (fonctionne sur Bosch)
        try:
            soap = '''<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
<s:Body><GetDeviceInformation xmlns="http://www.onvif.org/ver10/device/wsdl"/>
</s:Body></s:Envelope>'''
            r = requests.post(
                self._url("/onvif/device_service"), data=soap,
                headers={"Content-Type": "application/soap+xml"},
                auth=self.auth, timeout=self.timeout,
            )
            if r.status_code == 200:
                for tag in ["Manufacturer", "Model", "FirmwareVersion", "SerialNumber", "HardwareId"]:
                    m = re.search(rf'<[^:]*:?{tag}>([^<]*)</', r.text)
                    if m:
                        info[tag] = m.group(1)
        except Exception:
            pass
        if not info:
            try:
                r = requests.get(f"{self.base_url}/", auth=self.auth, timeout=self.timeout)
                info["http_status"] = r.status_code
                info["server"] = r.headers.get("Server", "?")
            except Exception:
                pass
        return info

    def _info_hanwha(self):
        r = self._get("/stw-cgi/system.cgi?msubmenu=deviceinfo&action=view")
        info = {}
        for line in r.text.strip().split("\n"):
            parts = line.strip().split("=", 1)
            if len(parts) == 2:
                info[parts[0].strip()] = parts[1].strip()
        return info

    def _info_axis(self):
        info = {}
        try:
            r = self._get("/axis-cgi/basicdeviceinfo.cgi")
            # Peut etre XML ou key=value
            if "<" in r.text:
                xml = self._strip_ns(r.text)
                root = ET.fromstring(xml)
                for child in root.iter():
                    tag = re.sub(r'\{[^}]*\}', '', child.tag)
                    if child.text and child.text.strip():
                        info[tag] = child.text.strip()
            else:
                for line in r.text.strip().split("\n"):
                    parts = line.strip().split("=", 1)
                    if len(parts) == 2:
                        info[parts[0].strip()] = parts[1].strip()
        except Exception:
            pass
        if not info:
            try:
                r = self._get("/axis-cgi/param.cgi?action=list&group=Brand")
                for line in r.text.strip().split("\n"):
                    parts = line.strip().split("=", 1)
                    if len(parts) == 2:
                        info[parts[0].strip()] = parts[1].strip()
            except Exception:
                pass
        return info

    def get_ptz_status(self):
        """Recupere la position PTZ actuelle."""
        try:
            r = self._get(f"/ISAPI/PTZCtrl/channels/{self.channel}/status")
            xml = self._strip_ns(r.text)
            root = ET.fromstring(xml)
            pos = root.find("AbsoluteHigh")
            if pos is not None:
                return {
                    "azimuth": float(pos.findtext("azimuth", "0")),
                    "elevation": float(pos.findtext("elevation", "0")),
                    "zoom": float(pos.findtext("absoluteZoom", "0")),
                }
        except Exception:
            pass
        return None

    ############################################################################
    # Presets (multi-marques)
    ############################################################################

    def list_presets(self):
        """Liste tous les presets configures (multi-marques)."""
        dispatch = {
            "hikvision": self._list_presets_hik,
            "dahua":     self._list_presets_dahua,
            "bosch":     self._list_presets_bosch,
            "hanwha":    self._list_presets_hanwha,
            "axis":      self._list_presets_axis,
            "uniview":   self._list_presets_hik,  # LAPI ~ ISAPI
        }
        fn = dispatch.get(self.brand, self._list_presets_hik)
        try:
            return fn()
        except Exception:
            return []

    def goto_preset(self, preset_id):
        """Deplace la camera vers un preset (multi-marques)."""
        dispatch = {
            "hikvision": self._goto_preset_hik,
            "dahua":     self._goto_preset_dahua,
            "bosch":     self._goto_preset_bosch,
            "hanwha":    self._goto_preset_hanwha,
            "axis":      self._goto_preset_axis,
            "uniview":   self._goto_preset_hik,
        }
        fn = dispatch.get(self.brand, self._goto_preset_hik)
        try:
            return fn(preset_id)
        except Exception:
            return False

    def set_preset(self, preset_id, name=""):
        """Sauvegarde la position actuelle comme preset."""
        if self.brand == "dahua":
            r = self._get(f"/cgi-bin/ptz.cgi?action=start&channel={self.channel-1}&code=SetPreset&arg1=0&arg2={preset_id}&arg3=0")
            return r.status_code == 200
        if self.brand == "hanwha":
            r = self._get(f"/stw-cgi/ptzcontrol.cgi?msubmenu=preset&action=set&Channel={self.channel-1}&Preset={preset_id}&Name={name or f'Preset_{preset_id}'}")
            return r.status_code == 200
        if self.brand == "axis":
            pname = name or f"preset{preset_id}"
            r = self._get(f"/axis-cgi/com/ptz.cgi?setserverpresetname={pname}&setserverpresetno={preset_id}")
            return r.status_code == 200
        # Hikvision / Uniview
        xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<PTZPreset>
  <id>{preset_id}</id>
  <presetName>{name or f'Preset_{preset_id}'}</presetName>
  <enabled>true</enabled>
</PTZPreset>"""
        r = self._put(
            f"/ISAPI/PTZCtrl/channels/{self.channel}/presets/{preset_id}",
            data=xml_body,
        )
        return r.status_code == 200

    def delete_preset(self, preset_id):
        """Supprime un preset."""
        if self.brand == "dahua":
            r = self._get(f"/cgi-bin/ptz.cgi?action=start&channel={self.channel-1}&code=ClearPreset&arg1=0&arg2={preset_id}&arg3=0")
            return r.status_code == 200
        if self.brand == "hanwha":
            r = self._get(f"/stw-cgi/ptzcontrol.cgi?msubmenu=preset&action=remove&Channel={self.channel-1}&Preset={preset_id}")
            return r.status_code == 200
        r = self._delete(f"/ISAPI/PTZCtrl/channels/{self.channel}/presets/{preset_id}")
        return r.status_code == 200

    # --- Hikvision ISAPI ---
    def _list_presets_hik(self):
        r = self._get(f"/ISAPI/PTZCtrl/channels/{self.channel}/presets")
        xml = self._strip_ns(r.text)
        root = ET.fromstring(xml)
        presets = []
        for preset in root.findall("PTZPreset"):
            pid = preset.findtext("id", "0")
            name = preset.findtext("presetName", "")
            enabled = preset.findtext("enabled", "false")
            if enabled == "true" or name:
                presets.append({"id": int(pid), "name": name, "enabled": enabled == "true"})
        return presets

    def _goto_preset_hik(self, preset_id):
        r = self._put(f"/ISAPI/PTZCtrl/channels/{self.channel}/presets/{preset_id}/goto")
        return r.status_code == 200

    # --- Dahua CGI ---
    def _list_presets_dahua(self):
        r = self._get(f"/cgi-bin/ptz.cgi?action=getPresets&channel={self.channel - 1}")
        presets = []
        for line in r.text.strip().split("\n"):
            line = line.strip()
            # Format: presets[0].Index=1 / presets[0].Name=xxx
            m = re.match(r'presets\[(\d+)\]\.(\w+)=(.*)', line)
            if m:
                idx = int(m.group(1))
                key = m.group(2)
                val = m.group(3)
                while len(presets) <= idx:
                    presets.append({"id": 0, "name": "", "enabled": True})
                if key == "Index":
                    presets[idx]["id"] = int(val)
                elif key == "Name":
                    presets[idx]["name"] = val
        return [p for p in presets if p["name"]]

    def _goto_preset_dahua(self, preset_id):
        r = self._get(f"/cgi-bin/ptz.cgi?action=start&channel={self.channel-1}&code=GotoPreset&arg1=0&arg2={preset_id}&arg3=0")
        return r.status_code == 200

    # --- Bosch ONVIF ---
    def _bosch_get_profile_token(self):
        """Recupere le premier ProfileToken ONVIF de la camera Bosch."""
        soap = '''<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
<s:Body><trt:GetProfiles/></s:Body></s:Envelope>'''
        r = requests.post(
            self._url("/onvif/media_service"), data=soap,
            headers={"Content-Type": "application/soap+xml"},
            auth=self.auth, timeout=self.timeout,
        )
        tokens = re.findall(r'token="([^"]+)"', r.text)
        return tokens[0] if tokens else "0"

    def _list_presets_bosch(self):
        token = self._bosch_get_profile_token()
        soap = f'''<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:ptz="http://www.onvif.org/ver20/ptz/wsdl">
<s:Body>
<ptz:GetPresets>
<ptz:ProfileToken>{token}</ptz:ProfileToken>
</ptz:GetPresets>
</s:Body></s:Envelope>'''
        r = requests.post(
            self._url("/onvif/ptz_service"), data=soap,
            headers={"Content-Type": "application/soap+xml"},
            auth=self.auth, timeout=self.timeout,
        )
        import html
        presets = []
        for m in re.finditer(r'token="([^"]+)"[^>]*>.*?<[^:]*:?Name>([^<]*)</[^:]*:?Name>', r.text, re.DOTALL):
            pid = m.group(1)
            name = html.unescape(html.unescape(m.group(2)))  # double unescape pour &amp;amp;
            if name:
                try:
                    presets.append({"id": int(pid), "name": name, "enabled": True})
                except ValueError:
                    presets.append({"id": 0, "name": f"{pid}: {name}", "enabled": True})
        return presets

    def _goto_preset_bosch(self, preset_id):
        token = self._bosch_get_profile_token()
        soap = f'''<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:ptz="http://www.onvif.org/ver20/ptz/wsdl">
<s:Body>
<ptz:GotoPreset>
<ptz:ProfileToken>{token}</ptz:ProfileToken>
<ptz:PresetToken>{preset_id}</ptz:PresetToken>
</ptz:GotoPreset>
</s:Body></s:Envelope>'''
        r = requests.post(
            self._url("/onvif/ptz_service"), data=soap,
            headers={"Content-Type": "application/soap+xml"},
            auth=self.auth, timeout=self.timeout,
        )
        return r.status_code == 200 and "Fault" not in r.text

    # --- Hanwha/Samsung Wisenet STW-CGI ---
    def _list_presets_hanwha(self):
        presets = []
        try:
            r = self._get(f"/stw-cgi/ptzcontrol.cgi?msubmenu=preset&action=view&Channel={self.channel - 1}")
            # Format: Preset.0.Name=xxx / Preset.0.Number=1
            data = {}
            for line in r.text.strip().split("\n"):
                m = re.match(r'Preset\.(\d+)\.(\w+)=(.*)', line.strip())
                if m:
                    idx = int(m.group(1))
                    key = m.group(2)
                    val = m.group(3)
                    if idx not in data:
                        data[idx] = {}
                    data[idx][key] = val
            for idx in sorted(data.keys()):
                d = data[idx]
                name = d.get("Name", "")
                pid = int(d.get("Number", d.get("Index", idx)))
                if name:
                    presets.append({"id": pid, "name": name, "enabled": True})
        except Exception:
            pass
        return presets

    def _goto_preset_hanwha(self, preset_id):
        r = self._get(f"/stw-cgi/ptzcontrol.cgi?msubmenu=preset&action=move&Channel={self.channel - 1}&Preset={preset_id}")
        return r.status_code == 200

    # --- Axis VAPIX ---
    def _list_presets_axis(self):
        presets = []
        try:
            r = self._get("/axis-cgi/com/ptz.cgi?query=presetposall")
            # Format: presetposno1=name1\npresetposno2=name2
            for line in r.text.strip().split("\n"):
                m = re.match(r'presetposno(\d+)=(.*)', line.strip())
                if m:
                    presets.append({"id": int(m.group(1)), "name": m.group(2), "enabled": True})
        except Exception:
            pass
        return presets

    def _goto_preset_axis(self, preset_id):
        r = self._get(f"/axis-cgi/com/ptz.cgi?gotoserverpresetno={preset_id}")
        return r.status_code == 200

    ############################################################################
    # Patrouilles
    ############################################################################

    def list_patrols(self):
        """Liste les patrouilles configurees."""
        try:
            r = self._get(f"/ISAPI/PTZCtrl/channels/{self.channel}/patrols")
            xml = self._strip_ns(r.text)
            root = ET.fromstring(xml)
            patrols = []
            for patrol in root.findall("PTZPatrol"):
                pid = patrol.findtext("id", "0")
                name = patrol.findtext("patrolName", "")
                enabled = patrol.findtext("enabled", "false")
                patrols.append({
                    "id": int(pid),
                    "name": name,
                    "enabled": enabled == "true",
                })
            return patrols
        except Exception:
            return []

    def start_patrol(self, patrol_id):
        """Lance une patrouille."""
        r = self._put(f"/ISAPI/PTZCtrl/channels/{self.channel}/patrols/{patrol_id}/start")
        return r.status_code == 200

    def stop_patrol(self, patrol_id):
        """Stoppe une patrouille."""
        r = self._put(f"/ISAPI/PTZCtrl/channels/{self.channel}/patrols/{patrol_id}/stop")
        return r.status_code == 200

    ############################################################################
    # Mouvement PTZ continu
    ############################################################################

    def move(self, pan=0, tilt=0, zoom=0):
        """Mouvement continu PTZ. Valeurs de -100 a 100."""
        xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<PTZData>
  <pan>{pan}</pan>
  <tilt>{tilt}</tilt>
  <zoom>{zoom}</zoom>
</PTZData>"""
        r = self._put(
            f"/ISAPI/PTZCtrl/channels/{self.channel}/continuous",
            data=xml_body,
        )
        return r.status_code == 200

    def stop_move(self):
        """Stoppe tout mouvement PTZ."""
        return self.move(0, 0, 0)

    def move_left(self, speed=50):
        return self.move(pan=-speed)

    def move_right(self, speed=50):
        return self.move(pan=speed)

    def move_up(self, speed=50):
        return self.move(tilt=speed)

    def move_down(self, speed=50):
        return self.move(tilt=-speed)

    def zoom_in(self, speed=50):
        return self.move(zoom=speed)

    def zoom_out(self, speed=50):
        return self.move(zoom=-speed)

    ############################################################################
    # Mouvement absolu
    ############################################################################

    def goto_position(self, azimuth, elevation, zoom):
        """Deplace la camera vers une position absolue.
        azimuth: 0-3600 (degres x10), elevation: -900 a 900, zoom: 10-400 (x10)
        """
        xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<PTZData>
  <AbsoluteHigh>
    <azimuth>{azimuth}</azimuth>
    <elevation>{elevation}</elevation>
    <absoluteZoom>{zoom}</absoluteZoom>
  </AbsoluteHigh>
</PTZData>"""
        r = self._put(
            f"/ISAPI/PTZCtrl/channels/{self.channel}/absolute",
            data=xml_body,
        )
        return r.status_code == 200

    ############################################################################
    # Capture d'image
    ############################################################################

    def capture_image(self, save_path=None):
        """Capture une image depuis la camera."""
        r = requests.get(
            self._url(f"/ISAPI/Streaming/channels/{self.channel}01/picture"),
            auth=self.auth,
            timeout=self.timeout,
            stream=True,
        )
        r.raise_for_status()

        if save_path is None:
            ts = time.strftime("%Y%m%d_%H%M%S")
            safe_name = self.name.replace(" ", "_")
            save_path = os.path.join(
                os.path.dirname(__file__),
                "hik_images",
                f"capture_{safe_name}_{ts}.jpg",
            )
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return save_path

    ############################################################################
    # Wiper / lumiere
    ############################################################################

    def wiper(self):
        """Active le wiper (un cycle)."""
        xml_body = """<?xml version="1.0" encoding="UTF-8"?>
<AuxCtrl>
  <auxiliaryAction>wiper</auxiliaryAction>
</AuxCtrl>"""
        try:
            r = self._put(
                f"/ISAPI/PTZCtrl/channels/{self.channel}/auxcontrols/1",
                data=xml_body,
            )
            return r.status_code == 200
        except Exception:
            return False

    def light_on(self):
        """Allume la lumiere supplementaire."""
        xml_body = """<?xml version="1.0" encoding="UTF-8"?>
<AuxCtrl>
  <auxiliaryAction>lightOn</auxiliaryAction>
</AuxCtrl>"""
        try:
            r = self._put(
                f"/ISAPI/PTZCtrl/channels/{self.channel}/auxcontrols/1",
                data=xml_body,
            )
            return r.status_code == 200
        except Exception:
            return False

    def light_off(self):
        """Eteint la lumiere supplementaire."""
        xml_body = """<?xml version="1.0" encoding="UTF-8"?>
<AuxCtrl>
  <auxiliaryAction>lightOff</auxiliaryAction>
</AuxCtrl>"""
        try:
            r = self._put(
                f"/ISAPI/PTZCtrl/channels/{self.channel}/auxcontrols/1",
                data=xml_body,
            )
            return r.status_code == 200
        except Exception:
            return False

    ############################################################################
    # Focus / Lens
    ############################################################################

    def lens_init(self):
        """Reinitialise l'objectif (lens initialization)."""
        # Methode 1 : focus initialisation via ISAPI
        for url in [
            f"/ISAPI/System/Video/inputs/channels/{self.channel}/focus/initializing",
            f"/ISAPI/System/Video/inputs/channels/{self.channel}/focus",
        ]:
            try:
                xml_body = """<?xml version="1.0" encoding="UTF-8"?>
<FocusConfiguration>
  <focusStyle>INITIALIZE</focusStyle>
</FocusConfiguration>"""
                r = self._put(url, data=xml_body)
                if r.status_code == 200:
                    return True
            except Exception:
                continue
        # Methode 2 : via auxcontrols
        try:
            xml_body = """<?xml version="1.0" encoding="UTF-8"?>
<AuxCtrl>
  <auxiliaryAction>initLens</auxiliaryAction>
</AuxCtrl>"""
            r = self._put(
                f"/ISAPI/PTZCtrl/channels/{self.channel}/auxcontrols/1",
                data=xml_body,
            )
            return r.status_code == 200
        except Exception:
            return False

    def autofocus(self):
        """Declenche un autofocus."""
        try:
            xml_body = """<?xml version="1.0" encoding="UTF-8"?>
<FocusConfiguration>
  <focusStyle>AUTO</focusStyle>
</FocusConfiguration>"""
            r = self._put(
                f"/ISAPI/System/Video/inputs/channels/{self.channel}/focus",
                data=xml_body,
            )
            return r.status_code == 200
        except Exception:
            return False

    ############################################################################
    # Home position
    ############################################################################

    def get_home_position(self):
        """Recupere la home position."""
        try:
            r = self._get(f"/ISAPI/PTZCtrl/channels/{self.channel}/homePosition")
            xml = self._strip_ns(r.text)
            root = ET.fromstring(xml)
            return {
                "azimuth": float(root.findtext("azimuth", "0")),
                "elevation": float(root.findtext("elevation", "0")),
                "zoom": float(root.findtext("absoluteZoom", "0")),
            }
        except Exception:
            return None

    def set_home_position(self):
        """Definit la position actuelle comme home position."""
        r = self._put(f"/ISAPI/PTZCtrl/channels/{self.channel}/homePosition")
        return r.status_code == 200

    def goto_home(self):
        """Va a la home position."""
        r = self._put(f"/ISAPI/PTZCtrl/channels/{self.channel}/homePosition/goto")
        return r.status_code == 200

    ############################################################################
    # Park action (retour auto apres inactivite)
    ############################################################################

    def get_park_action(self):
        """Recupere la config du park action."""
        try:
            r = self._get(f"/ISAPI/PTZCtrl/channels/{self.channel}/parkAction")
            xml = self._strip_ns(r.text)
            root = ET.fromstring(xml)
            return {
                "enabled": root.findtext("enabled", "false") == "true",
                "park_time": int(root.findtext("parkTime", "0")),
                "action_type": root.findtext("actionType", ""),
                "action_id": int(root.findtext("actionID", "0")),
            }
        except Exception:
            return None

    def set_park_action(self, enabled=True, park_time=30, action_type="preset", action_id=1):
        """Configure le park action.
        action_type: preset, patrol, pattern, scan, homePosition
        """
        xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<ParkAction>
  <enabled>{"true" if enabled else "false"}</enabled>
  <parkTime>{park_time}</parkTime>
  <actionType>{action_type}</actionType>
  <actionID>{action_id}</actionID>
</ParkAction>"""
        r = self._put(
            f"/ISAPI/PTZCtrl/channels/{self.channel}/parkAction",
            data=xml_body,
        )
        return r.status_code == 200

    ############################################################################
    # Mode jour/nuit
    ############################################################################

    def get_daynight_mode(self):
        """Recupere le mode jour/nuit actuel."""
        try:
            r = self._get(f"/ISAPI/Image/channels/{self.channel}/ircutFilter")
            xml = self._strip_ns(r.text)
            root = ET.fromstring(xml)
            return root.findtext("IrcutFilterType", "unknown")
        except Exception:
            return "unknown"

    def set_daynight_mode(self, mode):
        """Change le mode jour/nuit. mode: day, night, auto"""
        xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<IrcutFilter>
  <IrcutFilterType>{mode}</IrcutFilterType>
</IrcutFilter>"""
        r = self._put(
            f"/ISAPI/Image/channels/{self.channel}/ircutFilter",
            data=xml_body,
        )
        return r.status_code == 200

    ############################################################################
    # Capacites camera
    ############################################################################

    def get_capabilities(self):
        """Recupere les capacites globales de la camera."""
        try:
            r = self._get("/ISAPI/System/capabilities")
            xml = self._strip_ns(r.text)
            root = ET.fromstring(xml)
            caps = {}
            for child in root:
                tag = re.sub(r'\{[^}]*\}', '', child.tag)
                if child.text and child.text.strip():
                    caps[tag] = child.text.strip()
                elif len(child):
                    sub = {}
                    for sc in child:
                        st = re.sub(r'\{[^}]*\}', '', sc.tag)
                        if sc.text and sc.text.strip():
                            sub[st] = sc.text.strip()
                    if sub:
                        caps[tag] = sub
            return caps
        except Exception:
            return {}

    def get_smart_capabilities(self):
        """Recupere les capacites Smart/VCA de la camera."""
        try:
            r = self._get("/ISAPI/Smart/capabilities")
            xml = self._strip_ns(r.text)
            root = ET.fromstring(xml)
            caps = []
            for child in root:
                tag = re.sub(r'\{[^}]*\}', '', child.tag)
                caps.append(tag)
            return caps
        except Exception:
            return []

    def get_event_capabilities(self):
        """Recupere les capacites evenements."""
        try:
            r = self._get("/ISAPI/Event/capabilities")
            xml = self._strip_ns(r.text)
            root = ET.fromstring(xml)
            caps = []
            for child in root:
                tag = re.sub(r'\{[^}]*\}', '', child.tag)
                caps.append(tag)
            return caps
        except Exception:
            return []

    def get_ptz_capabilities(self):
        """Recupere les capacites PTZ."""
        try:
            r = self._get(f"/ISAPI/PTZCtrl/channels/{self.channel}/capabilities")
            xml = self._strip_ns(r.text)
            root = ET.fromstring(xml)
            caps = {}
            for child in root:
                tag = re.sub(r'\{[^}]*\}', '', child.tag)
                if child.text and child.text.strip():
                    caps[tag] = child.text.strip()
            return caps
        except Exception:
            return {}

    ############################################################################
    # Smart Events (lecture config)
    ############################################################################

    SMART_EVENT_ENDPOINTS = {
        "intrusion":        "FieldDetection",
        "linedetection":    "LineDetection",
        "regionentrance":   "RegionEntrance",
        "regionexit":       "RegionExiting",
        "baggage":          "UnattendedBaggage",
        "objectremoval":    "ObjectRemoval",
        "loiter":           "LoiterDetection",
        "gathering":        "PeopleGathering",
        "fastmoving":       "FastMoving",
        "parking":          "ParkingDetection",
        "audio":            "AudioDetection",
        "scenechange":      "SceneChangeDetection",
        "defocus":          "DefocusDetection",
        "vibration":        "VibrationDetection",
        "peoplecounting":   "PeopleCounting",
        "facedetect":       "FaceDetect",
        "mixedtarget":      "mixedTargetDetection",
        "vehicledetect":    "vehicleDetect",
    }

    def get_smart_event_config(self, event_key):
        """Recupere la config d'un smart event.
        event_key: intrusion, linedetection, parking, etc. (voir SMART_EVENT_ENDPOINTS)
        """
        endpoint = self.SMART_EVENT_ENDPOINTS.get(event_key.lower())
        if not endpoint:
            return None

        # Essayer les 2 formats d'URL possibles
        for url_pattern in [
            f"/ISAPI/Smart/{endpoint}/{self.channel}",
            f"/ISAPI/Smart/{endpoint}/channels/{self.channel}",
        ]:
            try:
                r = self._get(url_pattern)
                xml = self._strip_ns(r.text)
                root = ET.fromstring(xml)
                return self._parse_smart_config(root)
            except Exception:
                continue
        return None

    def _parse_smart_config(self, root):
        """Parse recursivement la config d'un smart event XML."""
        result = {}
        for child in root:
            tag = re.sub(r'\{[^}]*\}', '', child.tag)
            if len(child) == 0:
                text = child.text.strip() if child.text else ""
                result[tag] = text
            else:
                # Verifier si c'est une liste (plusieurs enfants avec le meme tag)
                child_tags = [re.sub(r'\{[^}]*\}', '', c.tag) for c in child]
                if len(set(child_tags)) == 1 and len(child_tags) > 1:
                    result[tag] = [self._parse_smart_config(c) for c in child]
                else:
                    result[tag] = self._parse_smart_config(child)
        return result

    def set_smart_event_enabled(self, event_key, enabled=True):
        """Active ou desactive un smart event.
        Lit la config actuelle, change enabled, et renvoie.
        """
        endpoint = self.SMART_EVENT_ENDPOINTS.get(event_key.lower())
        if not endpoint:
            return False

        for url_pattern in [
            f"/ISAPI/Smart/{endpoint}/{self.channel}",
            f"/ISAPI/Smart/{endpoint}/channels/{self.channel}",
        ]:
            try:
                r = self._get(url_pattern)
                # Modifier le champ enabled dans le XML brut
                xml_text = r.text
                enabled_str = "true" if enabled else "false"
                # Remplacer <enabled>true/false</enabled>
                xml_text = re.sub(
                    r'<enabled>(true|false)</enabled>',
                    f'<enabled>{enabled_str}</enabled>',
                    xml_text,
                    count=1,
                )
                r2 = self._put(url_pattern, data=xml_text.encode("utf-8"))
                return r2.status_code == 200
            except Exception:
                continue
        return False

    def list_smart_events_status(self):
        """Liste le status de tous les smart events connus."""
        results = []
        for key, endpoint in self.SMART_EVENT_ENDPOINTS.items():
            for url_pattern in [
                f"/ISAPI/Smart/{endpoint}/{self.channel}",
                f"/ISAPI/Smart/{endpoint}/channels/{self.channel}",
            ]:
                try:
                    r = self._get(url_pattern)
                    xml = self._strip_ns(r.text)
                    root = ET.fromstring(xml)
                    enabled = root.findtext("enabled", "?")
                    results.append({
                        "key": key,
                        "endpoint": endpoint,
                        "enabled": enabled,
                        "available": True,
                    })
                    break
                except Exception:
                    continue
            else:
                results.append({
                    "key": key,
                    "endpoint": endpoint,
                    "enabled": "?",
                    "available": False,
                })
        return results

    ############################################################################
    # Privacy Masks (multi-marques)
    ############################################################################

    def get_privacy_masks(self):
        """Recupere la liste des masques de vie privee (multi-marques)."""
        dispatch = {
            "hikvision": self._get_privacy_hik,
            "dahua":     self._get_privacy_dahua,
            "bosch":     self._get_privacy_bosch,
            "hanwha":    self._get_privacy_hanwha,
            "axis":      self._get_privacy_axis,
            "uniview":   self._get_privacy_hik,
        }
        fn = dispatch.get(self.brand, self._get_privacy_hik)
        try:
            return fn()
        except Exception:
            return None

    def set_privacy_masks_enabled(self, enabled):
        """Active ou desactive les masques de vie privee (multi-marques)."""
        dispatch = {
            "hikvision": self._set_privacy_hik,
            "dahua":     self._set_privacy_dahua,
            "bosch":     self._set_privacy_bosch,
            "hanwha":    self._set_privacy_hanwha,
            "axis":      self._set_privacy_axis,
            "uniview":   self._set_privacy_hik,
        }
        fn = dispatch.get(self.brand, self._set_privacy_hik)
        try:
            return fn(enabled)
        except Exception:
            return False

    # --- Hikvision ISAPI ---
    def _hik_get_all_channels(self):
        """Detecte tous les channels video de la camera (TandemVu, multi-capteur, etc.)."""
        try:
            r = self._get("/ISAPI/System/Video/inputs/channels")
            xml = self._strip_ns(r.text)
            root = ET.fromstring(xml)
            channels = []
            for ch in root.findall(".//VideoInputChannel"):
                cid = int(ch.findtext("id", "0"))
                desc = ch.findtext("channelDescription", "")
                if cid > 0:
                    channels.append({"id": cid, "description": desc})
            return channels if channels else [{"id": self.channel, "description": ""}]
        except Exception:
            return [{"id": self.channel, "description": ""}]

    def _get_privacy_hik(self):
        channels = self._hik_get_all_channels()
        all_masks = []
        any_enabled = False
        for ch in channels:
            try:
                r = self._get(f"/ISAPI/System/Video/inputs/channels/{ch['id']}/privacyMask")
                xml = self._strip_ns(r.text)
                root = ET.fromstring(xml)
                ch_enabled = root.findtext("enabled", "false") == "true"
                if ch_enabled:
                    any_enabled = True
                desc = ch.get("description", "")
                prefix = f"[Ch{ch['id']}{' '+desc if desc else ''}] "
                for region in root.findall(".//PrivacyMaskRegion"):
                    all_masks.append({
                        "id": int(region.findtext("id", "0")),
                        "enabled": region.findtext("enabled", "false") == "true",
                        "name": prefix + region.findtext("name", region.findtext("regionName", "")),
                    })
            except Exception:
                pass
        return {"enabled": any_enabled, "masks": all_masks}

    def _set_privacy_hik(self, enabled):
        channels = self._hik_get_all_channels()
        enabled_str = "true" if enabled else "false"
        all_ok = True
        for ch in channels:
            try:
                r = self._get(f"/ISAPI/System/Video/inputs/channels/{ch['id']}/privacyMask")
                xml_text = r.text
                xml_text = re.sub(
                    r'(<enabled>)(true|false)(</enabled>)',
                    rf'\g<1>{enabled_str}\3',
                    xml_text,
                    count=1,
                )
                r2 = self._put(
                    f"/ISAPI/System/Video/inputs/channels/{ch['id']}/privacyMask",
                    data=xml_text.encode("utf-8"),
                )
                if r2.status_code != 200:
                    all_ok = False
            except Exception:
                all_ok = False
        return all_ok

    # --- Dahua CGI ---
    def _get_privacy_dahua(self):
        r = self._get(f"/cgi-bin/configManager.cgi?action=getConfig&name=VideoWidget[{self.channel - 1}]")
        enabled = "EncodeBlend.PrivacyMasking[0].Enable=true" in r.text
        masks = []
        for m in re.finditer(r'PrivacyMasking\[(\d+)\]\.Enable=(true|false)', r.text):
            masks.append({"id": int(m.group(1)), "enabled": m.group(2) == "true", "name": f"Mask {m.group(1)}"})
        return {"enabled": enabled, "masks": masks}

    def _set_privacy_dahua(self, enabled):
        val = "true" if enabled else "false"
        # Activer/desactiver tous les masques (jusqu'a 4)
        params = "&".join(
            f"VideoWidget[{self.channel - 1}].EncodeBlend.PrivacyMasking[{i}].Enable={val}"
            for i in range(4)
        )
        r = self._get(f"/cgi-bin/configManager.cgi?action=setConfig&{params}")
        return r.status_code == 200

    # --- Bosch ---
    def _get_privacy_bosch(self):
        # Privacy masks Bosch non accessibles via API HTTP/ONVIF
        # Elles se configurent uniquement via l'interface web ou Bosch Configuration Manager
        return None

    def _set_privacy_bosch(self, enabled):
        # Non supporte via API sur Bosch AUTODOME
        return False

    # --- Hanwha/Samsung Wisenet ---
    def _get_privacy_hanwha(self):
        try:
            r = self._get(f"/stw-cgi/media.cgi?msubmenu=privacymask&action=view&Channel={self.channel - 1}")
            enabled = "Enable=True" in r.text or "Enable=true" in r.text
            masks = []
            for m in re.finditer(r'PrivacyMask\.(\d+)\.Enable=(True|False|true|false)', r.text):
                masks.append({"id": int(m.group(1)), "enabled": m.group(2).lower() == "true", "name": f"Mask {m.group(1)}"})
            return {"enabled": enabled, "masks": masks}
        except Exception:
            return None

    def _set_privacy_hanwha(self, enabled):
        val = "True" if enabled else "False"
        # Lire d'abord pour connaitre les masques existants
        try:
            r = self._get(f"/stw-cgi/media.cgi?msubmenu=privacymask&action=view&Channel={self.channel - 1}")
            mask_ids = re.findall(r'PrivacyMask\.(\d+)\.Enable=', r.text)
            if mask_ids:
                params = "&".join(f"PrivacyMask.{mid}.Enable={val}" for mid in mask_ids)
                r2 = self._get(f"/stw-cgi/media.cgi?msubmenu=privacymask&action=set&Channel={self.channel - 1}&{params}")
                return r2.status_code == 200
        except Exception:
            pass
        return False

    # --- Axis VAPIX ---
    def _get_privacy_axis(self):
        try:
            r = self._get("/axis-cgi/param.cgi?action=list&group=PrivacyMask")
            enabled = ".Enabled=yes" in r.text
            masks = []
            for m in re.finditer(r'PrivacyMask\.M(\d+)\.Enabled=(yes|no)', r.text):
                masks.append({"id": int(m.group(1)), "enabled": m.group(2) == "yes", "name": f"Mask {m.group(1)}"})
            return {"enabled": enabled, "masks": masks}
        except Exception:
            return None

    def _set_privacy_axis(self, enabled):
        val = "yes" if enabled else "no"
        try:
            # Lire les masques existants
            r = self._get("/axis-cgi/param.cgi?action=list&group=PrivacyMask")
            mask_ids = re.findall(r'PrivacyMask\.M(\d+)\.Enabled=', r.text)
            for mid in mask_ids:
                self._get(f"/axis-cgi/param.cgi?action=update&PrivacyMask.M{mid}.Enabled={val}")
            return True
        except Exception:
            return False

    ############################################################################
    # Systeme
    ############################################################################

    def reboot(self):
        """Redemarrer la camera."""
        r = self._put("/ISAPI/System/reboot")
        return r.status_code == 200


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
