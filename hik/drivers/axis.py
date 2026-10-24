# Pilote Axis (VAPIX, Digest) de HikCamera (hik/drivers/__init__.py).
# Methodes appelees par les repartiteurs de base.py selon `brand`. Regles
# d'usage (verifiees sur le parc) : docs/claude/cameras.md, section Pilotage.

import re


class AxisMixin:
    """Axis VAPIX : infos, presets (masques non pilotables)."""

    def _info_axis(self):
        # VAPIX param.cgi : present des firmwares 5.x (2014) aux 11.x, en Digest
        info = self._kv(self._get("/axis-cgi/param.cgi?action=list&group=Brand"))
        try:
            info.update(self._kv(self._get("/axis-cgi/param.cgi?action=list&group=Properties.Firmware")))
        except Exception:
            pass
        info["model"] = info.get("root.Brand.ProdShortName") or info.get("root.Brand.ProdNbr", "")
        info["firmwareVersion"] = info.get("root.Properties.Firmware.Version", "")
        return info

    def _list_presets_axis(self):
        # Format : presetposno1=name1 ; "PTZ disabled" sur les cameras fixes (liste vide)
        r = self._get(f"/axis-cgi/com/ptz.cgi?query=presetposall&camera={self.channel}")
        presets = []
        for k, v in self._kv(r).items():
            m = re.match(r'presetposno(\d+)$', k)
            if m:
                presets.append({"id": int(m.group(1)), "name": v or "Preset %s" % m.group(1), "enabled": True})
        return sorted(presets, key=lambda p: p["id"])

    def _goto_preset_axis(self, preset_id):
        r = self._get(f"/axis-cgi/com/ptz.cgi?gotoserverpresetno={int(preset_id)}&camera={self.channel}")
        # Succes = 204 sans contenu ; une erreur VAPIX arrive en 200 avec "Error"
        if "error" in r.content.decode("utf-8", "replace").lower():
            raise RuntimeError("Refus VAPIX : %s" % r.content[:120].decode("utf-8", "replace").strip())
        return True

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

    # get_privacy_masks / set_privacy_masks_enabled (base.py) et les methodes
    # _get_privacy_* / _set_privacy_* : outil en ligne de commande hik_control.py
    # seulement (historique, non verifie sur le parc). Cockpit utilise privacy().
