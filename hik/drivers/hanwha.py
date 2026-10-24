# Pilote Hanwha / Wisenet (SUNAPI, Digest) de HikCamera (hik/drivers/__init__.py).
# Methodes appelees par les repartiteurs de base.py selon `brand`. Regles
# d'usage (verifiees sur le parc) : docs/claude/cameras.md, section Pilotage.

import re


class HanwhaMixin:
    """Hanwha SUNAPI : erreurs en HTTP 200 avec un corps NG (_hanwha_get)."""

    def _hanwha_get(self, path):
        """Requete SUNAPI : les erreurs arrivent en HTTP 200 avec un corps "NG"."""
        r = self._get(path)
        body = r.content.decode("utf-8", "replace").strip()
        if body.startswith("NG"):
            m = re.search(r"Error Code:\s*(\d+)", body)
            err = RuntimeError("Refus SUNAPI (code %s)" % (m.group(1) if m else "?"))
            err.code = m.group(1) if m else ""
            raise err
        return r

    def _info_hanwha(self):
        info = self._kv(self._hanwha_get("/stw-cgi/system.cgi?msubmenu=deviceinfo&action=view"))
        info["model"] = info.get("Model", "")
        info["firmwareVersion"] = info.get("FirmwareVersion", "")
        return info

    def _list_presets_hanwha(self):
        # SUNAPI : ptzconfig.cgi (ptzcontrol.cgi repond "Action Not Found" en view).
        # Format : Channel.0.Preset.1.Name=Parking
        try:
            r = self._hanwha_get(f"/stw-cgi/ptzconfig.cgi?msubmenu=preset&action=view&Channel={self.channel - 1}")
        except RuntimeError as e:
            if getattr(e, "code", "") == "608":  # camera fixe : pas de PTZ
                return []
            raise
        presets = []
        for k, v in self._kv(r).items():
            m = re.match(r'Channel\.\d+\.Preset\.(\d+)\.Name$', k)
            if m:
                presets.append({"id": int(m.group(1)), "name": v or "Preset %s" % m.group(1), "enabled": True})
        return sorted(presets, key=lambda p: p["id"])

    def _goto_preset_hanwha(self, preset_id):
        self._hanwha_get(f"/stw-cgi/ptzcontrol.cgi?msubmenu=preset&action=control&Channel={self.channel - 1}&Preset={int(preset_id)}")
        return True

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

    # get_privacy_masks / set_privacy_masks_enabled (base.py) et les methodes
    # _get_privacy_* / _set_privacy_* : outil en ligne de commande hik_control.py
    # seulement (historique, non verifie sur le parc). Cockpit utilise privacy().

    # Masques de vie privee (Cockpit, verifie sur le parc le 21/10/2026)
    def _privacy_hanwha(self, set_to=None):
        ch = self.channel - 1
        if set_to is not None:
            r = self._get("/stw-cgi/image.cgi?msubmenu=privacy&action=set&Channel=%d&Enable=%s" % (ch, "True" if set_to else "False"))
            if r.text.lstrip().startswith("NG"):
                raise RuntimeError("Refus de la camera : %s" % " ".join(r.text.split())[:120])
        r = self._get("/stw-cgi/image.cgi?msubmenu=privacy&action=view&Channel=%d" % ch)
        if r.text.lstrip().startswith("NG"):
            raise RuntimeError("Masques illisibles : %s" % " ".join(r.text.split())[:120])
        on = re.search(r"Channel\.%d\.Enable=(True|False)" % ch, r.text, re.I)
        # Masques listes seulement quand l'interrupteur est actif sur certains firmwares
        masks = len(re.findall(r"MaskIndex=", r.text))
        return [{"no": self.channel, "masks": masks if masks else None, "enabled": bool(on and on.group(1).lower() == "true")}]
