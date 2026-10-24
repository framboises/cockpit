# Pilote Hikvision (ISAPI, Digest ; Uniview aussi) de HikCamera (hik/drivers/__init__.py).
# Methodes appelees par les repartiteurs de base.py selon `brand`. Regles
# d'usage (verifiees sur le parc) : docs/claude/cameras.md, section Pilotage.

import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote_plus

import requests


class HikvisionMixin:
    """Hikvision ISAPI : infos, presets, PTZ, essuie-glace, focus, home / park,
    jour-nuit, capacites, Smart Events, masques, redemarrage."""

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

    def _list_presets_hik(self):
        r = self._get(f"/ISAPI/PTZCtrl/channels/{self.channel}/presets")
        xml = self._strip_ns(r.text)
        root = ET.fromstring(xml)
        presets = []
        for preset in root.findall("PTZPreset"):
            pid = preset.findtext("id", "0")
            # Noms poses par SeeTec encodes comme une URL ("*+Vue+Nord+%28Po")
            name = unquote_plus(preset.findtext("presetName", ""))
            enabled = preset.findtext("enabled", "false")
            if enabled == "true" or name:
                presets.append({"id": int(pid), "name": name, "enabled": enabled == "true"})
        return presets

    def _goto_preset_hik(self, preset_id):
        r = self._put(f"/ISAPI/PTZCtrl/channels/{self.channel}/presets/{preset_id}/goto")
        return r.status_code == 200

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

    def reboot(self):
        """Redemarrer la camera."""
        r = self._put("/ISAPI/System/reboot")
        return r.status_code == 200

    # get_privacy_masks / set_privacy_masks_enabled (base.py) et les methodes
    # _get_privacy_* / _set_privacy_* : outil en ligne de commande hik_control.py
    # seulement (historique, non verifie sur le parc). Cockpit utilise privacy().

    # Masques de vie privee (Cockpit, verifie sur le parc le 21/10/2026)
    def _privacy_hik(self, set_to=None):
        """Canaux ayant au moins une zone dessinee ; set_to : bascule leur interrupteur."""
        xml = re.sub(r'xmlns="[^"]*"', "", self._get("/ISAPI/System/Video/inputs/channels").text)
        ids = [int(i) for i in re.findall(r"<VideoInputChannel[^>]*>\s*<id>(\d+)</id>", xml)] or [self.channel]
        chans = []
        for cid in ids:
            try:
                raw = self._get("/ISAPI/System/Video/inputs/channels/%d/privacyMask" % cid).text
            except requests.exceptions.HTTPError:
                continue  # canal sans fonction masque
            regions = len(re.findall(r"<PrivacyMaskRegion\b", raw))
            if not regions:
                continue
            if set_to is not None:
                body = re.sub(r"(<enabled>)(true|false)(</enabled>)", r"\g<1>%s\3" % ("true" if set_to else "false"), raw, count=1)
                self._put("/ISAPI/System/Video/inputs/channels/%d/privacyMask" % cid, data=body.encode("utf-8"))
                raw = self._get("/ISAPI/System/Video/inputs/channels/%d/privacyMask" % cid).text
            m = re.search(r"<enabled>(true|false)</enabled>", re.sub(r'xmlns="[^"]*"', "", raw))
            chans.append({"no": cid, "masks": regions, "enabled": bool(m and m.group(1) == "true")})
        return chans
