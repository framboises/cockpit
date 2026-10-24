# Socle de HikCamera (hik/drivers/__init__.py) : HTTP Digest, ONVIF, et
# repartition des operations multi-marques vers les pilotes (hikvision.py,
# dahua.py, bosch.py, hanwha.py, axis.py). Ajouter une marque = un module
# pilote + ses entrees dans les repartiteurs ci-dessous.

import os
import re
import time

import requests
from requests.auth import HTTPDigestAuth

# Dossier hik/ : destination par defaut de capture_image (comme avant le decoupage)
HIK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Marques dont Cockpit sait basculer les masques de vie privee (privacy())
PRIVACY_BRANDS = {"hikvision", "hanwha", "dahua", "bosch"}


class BaseCamera:
    """Connexion a un appareil (un canal) et operations multi-marques."""

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

    def get_device_info(self, raise_errors=False):
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
            if raise_errors:
                raise
            return {"_brand": self.brand, "_error": str(e)}

    @staticmethod
    def _kv(r):
        """Reponse texte cle=valeur (Dahua CGI, Hanwha SUNAPI), decodee en UTF-8."""
        info = {}
        for line in r.content.decode("utf-8", "replace").strip().splitlines():
            parts = line.strip().split("=", 1)
            if len(parts) == 2:
                info[parts[0].strip()] = parts[1].strip()
        return info

    def _onvif(self, service, body):
        """Requete SOAP ONVIF (Digest). Leve une erreur si la camera refuse."""
        env = ('<?xml version="1.0" encoding="utf-8"?>'
               '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
               '<s:Body>%s</s:Body></s:Envelope>' % body)
        r = requests.post(
            self._url(service), data=env,
            headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            auth=self.auth, timeout=self.timeout,
        )
        if r.status_code == 401:
            raise RuntimeError("Identifiants refuses par la camera (HTTP 401)")
        if r.status_code != 200 or "Fault>" in r.text:
            m = re.search(r'<[^>]*?:?Text[^>]*>([^<]*)</', r.text)
            raise RuntimeError("Refus ONVIF (HTTP %d)%s" % (r.status_code, " : " + m.group(1) if m else ""))
        return r.text

    def list_presets(self, raise_errors=False):
        """Liste tous les presets configures (multi-marques).

        raise_errors : remonte l'erreur au lieu de renvoyer une liste vide
        (Cockpit doit distinguer "aucun preset" de "camera injoignable")."""
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
            if raise_errors:
                raise
            return []

    def goto_preset(self, preset_id, raise_errors=False):
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
            if raise_errors:
                raise
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

    def capture_image(self, save_path=None):
        """Capture une image depuis la camera (Hikvision ISAPI, Bosch, Dahua, Hanwha)."""
        path = {
            "bosch": f"/snap.jpg?JpegCam={self.channel}",
            "dahua": f"/cgi-bin/snapshot.cgi?channel={self.channel}",
            "hanwha": f"/stw-cgi/video.cgi?msubmenu=snapshot&action=view&Channel={self.channel - 1}",
            "axis": f"/axis-cgi/jpg/image.cgi?camera={self.channel}",
        }.get(self.brand, f"/ISAPI/Streaming/channels/{self.channel}01/picture")
        r = requests.get(
            self._url(path),
            auth=self.auth,
            timeout=self.timeout,
            stream=True,
        )
        r.raise_for_status()
        if not r.headers.get("Content-Type", "").startswith("image/"):
            # Hanwha et Dahua repondent parfois 200 avec un texte d'erreur
            raise RuntimeError("La camera n'a pas renvoye d'image : %s" % r.content[:120].decode("utf-8", "replace").strip())

        if save_path is None:
            ts = time.strftime("%Y%m%d_%H%M%S")
            safe_name = self.name.replace(" ", "_")
            save_path = os.path.join(
                HIK_DIR,
                "hik_images",
                f"capture_{safe_name}_{ts}.jpg",
            )
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return save_path

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

    def privacy(self, set_to=None):
        """Masques de vie privee DEJA dessines sur la camera : etat par canal
        [{no, masks, enabled}] ; set_to True / False bascule leur interrupteur
        puis relit. Jamais de creation, deplacement ni suppression de zone."""
        fn = {"hikvision": self._privacy_hik, "hanwha": self._privacy_hanwha,
              "dahua": self._privacy_dahua, "bosch": self._privacy_bosch}.get(self.brand)
        if fn is None:
            raise RuntimeError("Masques non pilotables pour la marque %s" % self.brand)
        return fn(set_to)
