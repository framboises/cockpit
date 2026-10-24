# Pilote Bosch (ONVIF + RCP+, Digest) de HikCamera (hik/drivers/__init__.py).
# Methodes appelees par les repartiteurs de base.py selon `brand`. Regles
# d'usage (verifiees sur le parc) : docs/claude/cameras.md, section Pilotage.

import re

BOSCH_PRIV_CMD = "0x0bd8"  # RCP+ CONF_PRIV_MSK_POLY (base de connaissances Bosch / Keenfinity)


class BoschMixin:
    """Bosch : infos et presets en ONVIF, masques des domes en RCP+."""

    def _info_bosch(self):
        # ONVIF GetDeviceInformation, authentification Digest (compte "service",
        # verifie sur les AUTODOME 5000i et MIC 7000i, firmwares 6.41 a 7.90)
        r = self._onvif("/onvif/device_service",
                        '<GetDeviceInformation xmlns="http://www.onvif.org/ver10/device/wsdl"/>')
        info = {}
        for tag in ["Manufacturer", "Model", "FirmwareVersion", "SerialNumber", "HardwareId"]:
            m = re.search(rf'<[^>]*?:?{tag}>([^<]*)</', r)
            if m:
                info[tag] = m.group(1)
        # Memes cles que l'ISAPI Hikvision (lues par la page Cameras)
        info["model"] = info.get("Model", "")
        info["firmwareVersion"] = info.get("FirmwareVersion", "")
        return info

    def _bosch_get_profile_token(self):
        """Premier ProfileToken ONVIF (media) de la camera Bosch."""
        r = self._onvif("/onvif/media_service", '<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>')
        tokens = re.findall(r'<[^>]*?:?Profiles\b[^>]*?\btoken="([^"]+)"', r)
        return tokens[0] if tokens else "0"

    def _list_presets_bosch(self):
        import html
        token = self._bosch_get_profile_token()
        r = self._onvif("/onvif/ptz_service",
                        '<GetPresets xmlns="http://www.onvif.org/ver20/ptz/wsdl"><ProfileToken>%s</ProfileToken></GetPresets>' % token)
        presets = []
        for m in re.finditer(r'<[^>]*?:?Preset\b[^>]*?\btoken="([^"]+)"[^>]*>(.*?)</[^>]*?:?Preset>', r, re.DOTALL):
            n = re.search(r'<[^>]*?:?Name>([^<]*)</', m.group(2))
            name = html.unescape(html.unescape(n.group(1))).strip() if n else ""  # double unescape pour &amp;amp;
            try:
                pid = int(m.group(1))
            except ValueError:
                continue
            presets.append({"id": pid, "name": name or "Preset %d" % pid, "enabled": True})
        return sorted(presets, key=lambda p: p["id"])

    def _goto_preset_bosch(self, preset_id):
        token = self._bosch_get_profile_token()
        self._onvif("/onvif/ptz_service",
                    '<GotoPreset xmlns="http://www.onvif.org/ver20/ptz/wsdl"><ProfileToken>%s</ProfileToken>'
                    '<PresetToken>%d</PresetToken></GotoPreset>' % (token, int(preset_id)))
        return True

    def _get_privacy_bosch(self):
        # Privacy masks Bosch non accessibles via API HTTP/ONVIF
        # Elles se configurent uniquement via l'interface web ou Bosch Configuration Manager
        return None

    def _set_privacy_bosch(self, enabled):
        # Non supporte via API sur Bosch AUTODOME
        return False

    # get_privacy_masks / set_privacy_masks_enabled (base.py) et les methodes
    # _get_privacy_* / _set_privacy_* : outil en ligne de commande hik_control.py
    # seulement (historique, non verifie sur le parc). Cockpit utilise privacy().

    # Masques de vie privee (Cockpit, verifie sur le parc le 21/10/2026)
    def _privacy_bosch(self, set_to=None):
        """RCP+ (Digest) : type 0x0002 = un masque (polygone si longueur > 8),
        type 0x0003 = reglage global des domes, 1er octet 01 = masques desactives
        (case "Disable Masks" de l'interface web). Seul cet octet est reecrit, le
        reste de l'enregistrement est conserve (l'exemple Bosch remet a zero le
        3e octet, qui vaut 01 sur nos AUTODOME). Teste le 21/10/2026 sur
        10.34.1.36 (AUTODOME 5000i, 7 masques) : bascule visible, retour
        identique octet pour octet. Fixes Bosch sans 0x0003 : non pilotees."""
        recs = self._bosch_priv_read()
        drawn = sum(1 for tag, body in recs if tag == 2 and len(body) > 8)
        glob = [body for tag, body in recs if tag == 3]
        if not drawn or not glob:
            return []
        body = glob[0]
        if set_to is not None:
            new = bytes([0 if set_to else 1]) + body[1:]
            r = self._get("/rcp.xml?command=%s&type=P_OCTET&direction=WRITE&num=1&payload=0x%04x0003%s"
                         % (BOSCH_PRIV_CMD, len(new) + 4, new.hex()))
            if re.search(r"<err>", r.text):
                raise RuntimeError("Refus de la camera (RCP+ %s)" % re.search(r"<err>([^<]*)", r.text).group(1))
            body = [b for tag, b in self._bosch_priv_read() if tag == 3][0]
        return [{"no": self.channel, "masks": drawn, "enabled": body[:1] == b"\x00"}]

    def _bosch_priv_read(self):
        r = self._get("/rcp.xml?command=%s&type=P_OCTET&direction=READ&num=1" % BOSCH_PRIV_CMD)
        if re.search(r"<err>", r.text):
            raise RuntimeError("Masques illisibles (RCP+ %s)" % re.search(r"<err>([^<]*)", r.text).group(1))
        m = re.search(r"<str>(.*?)</str>", r.text, re.S)
        data = bytes.fromhex(re.sub(r"\s+", "", m.group(1))) if m else b""
        recs, i = [], 0
        while i + 4 <= len(data):  # enregistrements [longueur 2 octets][type 2 octets][contenu]
            ln, tag = int.from_bytes(data[i:i + 2], "big"), int.from_bytes(data[i + 2:i + 4], "big")
            if ln < 4:
                break
            recs.append((tag, data[i + 4:i + ln]))
            i += ln
        return recs
