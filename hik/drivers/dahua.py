# Pilote Dahua (CGI, Digest) de HikCamera (hik/drivers/__init__.py).
# Methodes appelees par les repartiteurs de base.py selon `brand`. Regles
# d'usage (verifiees sur le parc) : docs/claude/cameras.md, section Pilotage.

import re


class DahuaMixin:
    """Dahua CGI (domes SD) : infos, presets, masques."""

    def _info_dahua(self):
        r = self._get("/cgi-bin/magicBox.cgi?action=getSystemInfo")
        info = self._kv(r)
        # Memes cles que l'ISAPI Hikvision (lues par la page Cameras)
        info["model"] = info.get("deviceType", "")
        try:
            v = self._kv(self._get("/cgi-bin/magicBox.cgi?action=getSoftwareVersion")).get("version", "")
            info["firmwareVersion"] = v.split(",")[0]
        except Exception:
            pass
        return info

    def _list_presets_dahua(self):
        r = self._get(f"/cgi-bin/ptz.cgi?action=getPresets&channel={self.channel - 1}")
        # Format : presets[0].Index=1 / .Name=xxx / .PresetFunction=None / .Type=0
        rows = {}
        for k, v in self._kv(r).items():
            m = re.match(r'presets\[(\d+)\]\.(\w+)$', k)
            if m:
                rows.setdefault(int(m.group(1)), {})[m.group(2)] = v
        presets = []
        for row in rows.values():
            # PresetFunction autre que None = preset de fonction (pas une vue)
            if row.get("PresetFunction", "None") not in ("None", ""):
                continue
            try:
                pid = int(row.get("Index", ""))
            except ValueError:
                continue
            presets.append({"id": pid, "name": row.get("Name") or "Preset %d" % pid, "enabled": True})
        return sorted(presets, key=lambda p: p["id"])

    def _goto_preset_dahua(self, preset_id):
        r = self._get(f"/cgi-bin/ptz.cgi?action=start&channel={self.channel-1}&code=GotoPreset&arg1=0&arg2={preset_id}&arg3=0")
        return r.status_code == 200

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

    # get_privacy_masks / set_privacy_masks_enabled (base.py) et les methodes
    # _get_privacy_* / _set_privacy_* : outil en ligne de commande hik_control.py
    # seulement (historique, non verifie sur le parc). Cockpit utilise privacy().

    # Masques de vie privee (Cockpit, verifie sur le parc le 21/10/2026)
    def _privacy_dahua(self, set_to=None):
        ch = self.channel - 1
        txt = self._get("/cgi-bin/configManager.cgi?action=getConfig&name=PrivacyMasking").text
        slots = {}
        for k, _field, v in re.findall(r"PrivacyMasking\[%d\]\[(\d+)\]\.((?:Rect|Polygon)\[[\d\]\[]+\])=(-?\d+)" % ch, txt):
            slots.setdefault(int(k), False)
            if v != "0":
                slots[int(k)] = True
        drawn = sorted(i for i, used in slots.items() if used)
        if set_to is not None and drawn:
            params = "&".join("PrivacyMasking[%d][%d].Enable=%s" % (ch, i, "true" if set_to else "false") for i in drawn)
            r = self._get("/cgi-bin/configManager.cgi?action=setConfig&" + params)
            if "OK" not in r.text:
                raise RuntimeError("Refus de la camera : %s" % r.text.strip()[:120])
            txt = self._get("/cgi-bin/configManager.cgi?action=getConfig&name=PrivacyMasking").text
        on = [i for i in drawn if re.search(r"PrivacyMasking\[%d\]\[%d\]\.Enable=true" % (ch, i), txt)]
        return [{"no": self.channel, "masks": len(drawn), "enabled": bool(on)}] if drawn else []
