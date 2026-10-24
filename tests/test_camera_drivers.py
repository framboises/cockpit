"""Pilotes des cameras (hik/drivers) : URL envoyees par marque, lecture des
reponses, repartition. Aucun appel reseau : requests est remplace par un
double qui enregistre chaque requete et rend la reponse prevue."""
import pytest

import hik.drivers.base as base
import hik.drivers.hikvision as hikvision
from hik.drivers import HikCamera, PRIVACY_BRANDS


class FakeResponse:
    def __init__(self, text="", status=200, ctype="text/plain", content=None):
        self.text = text
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        self.content = content if content is not None else text.encode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(response=self)

    def iter_content(self, n):
        yield self.content


class FakeRequests:
    """Enregistre (methode, chemin) ; reponses par prefixe de chemin."""

    def __init__(self, routes=None):
        self.calls = []
        self.routes = routes or {}
        import requests
        self.exceptions = requests.exceptions

    def _answer(self, method, url, **kw):
        path = url.split(":80", 1)[1]
        self.calls.append((method, path))
        for prefix, resp in self.routes.items():
            if path.startswith(prefix):
                return resp
        return FakeResponse()

    def get(self, url, **kw):
        return self._answer("GET", url, **kw)

    def put(self, url, **kw):
        return self._answer("PUT", url, **kw)

    def post(self, url, **kw):
        return self._answer("POST", url, **kw)

    def delete(self, url, **kw):
        return self._answer("DELETE", url, **kw)


@pytest.fixture
def net(monkeypatch):
    fake = FakeRequests()
    monkeypatch.setattr(base, "requests", fake)
    monkeypatch.setattr(hikvision, "requests", fake)
    return fake


def cam(brand, channel=1):
    return HikCamera("Test", "10.0.0.1", 80, "u", "p", channel=channel, brand=brand)


def test_compatibilite_import_historique():
    from hik.hik_control import HikCamera as Ancien
    assert Ancien is HikCamera


@pytest.mark.parametrize("brand, channel, path", [
    ("hikvision", 4, "/ISAPI/Streaming/channels/401/picture"),
    ("uniview", 1, "/ISAPI/Streaming/channels/101/picture"),
    ("bosch", 2, "/snap.jpg?JpegCam=2"),
    ("dahua", 1, "/cgi-bin/snapshot.cgi?channel=1"),
    ("hanwha", 1, "/stw-cgi/video.cgi?msubmenu=snapshot&action=view&Channel=0"),
    ("axis", 1, "/axis-cgi/jpg/image.cgi?camera=1"),
])
def test_capture_par_marque(net, tmp_path, brand, channel, path):
    net.routes = {"/": FakeResponse(ctype="image/jpeg", content=b"JPEG")}
    out = cam(brand, channel).capture_image(str(tmp_path / "x.jpg"))
    assert net.calls == [("GET", path)]
    assert open(out, "rb").read() == b"JPEG"


def test_capture_refuse_un_texte(net, tmp_path):
    net.routes = {"/": FakeResponse("Error: busy", ctype="text/plain")}
    with pytest.raises(RuntimeError):
        cam("dahua").capture_image(str(tmp_path / "x.jpg"))


@pytest.mark.parametrize("brand, path", [
    ("hikvision", "/ISAPI/PTZCtrl/channels/1/presets/7/goto"),
    ("dahua", "/cgi-bin/ptz.cgi?action=start&channel=0&code=GotoPreset&arg1=0&arg2=7&arg3=0"),
    ("hanwha", "/stw-cgi/ptzcontrol.cgi?msubmenu=preset&action=control&Channel=0&Preset=7"),
    ("axis", "/axis-cgi/com/ptz.cgi?gotoserverpresetno=7&camera=1"),
])
def test_rappel_de_preset_par_marque(net, brand, path):
    assert cam(brand).goto_preset(7, raise_errors=True)
    assert net.calls[-1][1] == path


def test_presets_hikvision_noms_seetec_decodes(net):
    net.routes = {"/ISAPI/PTZCtrl/channels/1/presets": FakeResponse(
        '<PTZPresetList xmlns="http://www.hikvision.com/ver20/XMLSchema">'
        "<PTZPreset><id>1</id><presetName>*+Vue+Nord</presetName><enabled>true</enabled></PTZPreset>"
        "<PTZPreset><id>2</id><presetName></presetName><enabled>false</enabled></PTZPreset>"
        "</PTZPresetList>")}
    assert cam("hikvision").list_presets(raise_errors=True) == [{"id": 1, "name": "* Vue Nord", "enabled": True}]


def test_presets_dahua_sans_presets_de_fonction(net):
    net.routes = {"/cgi-bin/ptz.cgi": FakeResponse(
        "presets[0].Index=1\npresets[0].Name=Parking\npresets[0].PresetFunction=None\n"
        "presets[1].Index=2\npresets[1].Name=Scan\npresets[1].PresetFunction=AutoScan\n")}
    assert cam("dahua").list_presets(raise_errors=True) == [{"id": 1, "name": "Parking", "enabled": True}]


def test_presets_hanwha_camera_fixe(net):
    net.routes = {"/stw-cgi/ptzconfig.cgi": FakeResponse("NG\nError Code: 608\n")}
    assert cam("hanwha").list_presets(raise_errors=True) == []


def test_axis_erreur_vapix_en_200(net):
    net.routes = {"/axis-cgi/com/ptz.cgi": FakeResponse("Error: PTZ disabled")}
    with pytest.raises(RuntimeError):
        cam("axis").goto_preset(1, raise_errors=True)


def test_infos_dahua_cles_communes(net):
    net.routes = {"/cgi-bin/magicBox.cgi?action=getSystemInfo": FakeResponse("deviceType=SD6C\n"),
                  "/cgi-bin/magicBox.cgi?action=getSoftwareVersion": FakeResponse("version=2.800.0,build:2023\n")}
    info = cam("dahua").get_device_info(raise_errors=True)
    assert info["model"] == "SD6C" and info["firmwareVersion"] == "2.800.0" and info["_brand"] == "dahua"


def test_masques_repartition():
    assert PRIVACY_BRANDS == {"hikvision", "hanwha", "dahua", "bosch"}
    for b in PRIVACY_BRANDS:
        assert callable(getattr(cam(b), "_privacy_" + {"hikvision": "hik"}.get(b, b)))
    with pytest.raises(RuntimeError):
        cam("axis").privacy()


def test_masques_hanwha_bascule_puis_relit(net):
    net.routes = {"/stw-cgi/image.cgi?msubmenu=privacy&action=set": FakeResponse("OK"),
                  "/stw-cgi/image.cgi?msubmenu=privacy&action=view": FakeResponse("Channel.0.Enable=False\nMaskIndex=1\n")}
    out = cam("hanwha").privacy(set_to=False)
    assert [c[1] for c in net.calls] == ["/stw-cgi/image.cgi?msubmenu=privacy&action=set&Channel=0&Enable=False",
                                         "/stw-cgi/image.cgi?msubmenu=privacy&action=view&Channel=0"]
    assert out == [{"no": 1, "masks": 1, "enabled": False}]


def test_masques_dahua_seuls_les_emplacements_dessines(net):
    cfg = ("table.PrivacyMasking[0][0].Enable=true\ntable.PrivacyMasking[0][0].Rect[0]=10\n"
           "table.PrivacyMasking[0][1].Enable=false\ntable.PrivacyMasking[0][1].Rect[0]=0\n")
    net.routes = {"/cgi-bin/configManager.cgi?action=getConfig": FakeResponse(cfg),
                  "/cgi-bin/configManager.cgi?action=setConfig": FakeResponse("OK")}
    cam("dahua").privacy(set_to=False)
    sets = [p for m, p in net.calls if "setConfig" in p]
    assert sets == ["/cgi-bin/configManager.cgi?action=setConfig&PrivacyMasking[0][0].Enable=false"]
