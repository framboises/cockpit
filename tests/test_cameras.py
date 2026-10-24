"""Regles non negociables du domaine cameras (docs/claude/cameras.md).

- Cameras de piste : jamais pilotees (is_piste_device, verrou de make_hik_camera).
- Presets de fonction Hikvision (94 = redemarrage...) : jamais acceptes ni rappeles.
- Capture manuelle : une a la fois par appareil, delai par canal, vignette du bon canal.
"""
import os

import pytest

import cameras as C


# ---------------------------------------------------------------------------
# Cameras de piste
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("doc", [
    {"group_path": "Racine/Pistes/Bugatti", "ip": "10.34.1.5"},
    {"group_path": "Cam Courses", "ip": "10.34.1.5"},
    {"model": "DH-XVR5108HS", "ip": "10.34.1.5"},
    {"model": "DS-7316HQHI", "ip": "10.34.1.5"},
    {"model": "DS-9632NI", "ip": "10.34.1.5"},
    {"ip": "10.34.2.12"},
])
def test_piste_reconnue(doc):
    assert C.is_piste_device(doc)


def test_piste_exception_ip_seulement():
    # 10.34.2.180 (Technoparc Formation) : seul le critere IP est leve
    assert not C.is_piste_device({"ip": "10.34.2.180"})
    assert C.is_piste_device({"ip": "10.34.2.180", "group_path": "Pistes"})
    assert C.is_piste_device({"ip": "10.34.2.180", "model": "DH-XVR1B08"})


def test_hors_piste():
    assert not C.is_piste_device({"ip": "10.34.1.171", "model": "DS-2DP7D836", "group_path": "Parkings"})


def test_make_hik_camera_refuse_la_piste(monkeypatch):
    # Le verrou passe AVANT toute lecture d'identifiant ou tout envoi
    monkeypatch.setattr(C, "credentials_for", lambda d: pytest.fail("identifiants lus pour une camera de piste"))
    with pytest.raises(C.PisteCameraLocked):
        C.make_hik_camera({"ip": "10.34.2.12", "name": "XVR"})


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["94", "95", "33", "50", "92", "105", 94])
def test_presets_de_fonction_hik_refuses(value):
    assert C.view_preset_id(value, "hikvision") is None


@pytest.mark.parametrize("value", ["", None, "0", "301", "abc", "1;94", "-1"])
def test_presets_invalides(value):
    assert C.view_preset_id(value, "hikvision") is None


def test_presets_de_vue():
    assert C.view_preset_id("1", "hikvision") == 1
    assert C.view_preset_id(" 32 ", "hikvision") == 32
    assert C.view_preset_id("106", "hikvision") == 106
    # Les numeros reserves sont propres a Hikvision
    assert C.view_preset_id("94", "dahua") == 94
    # Marque inconnue : prudence, regle Hikvision
    assert C.view_preset_id("94") is None


def test_usage_capture_preset_de_fonction_efface():
    roles = C._clean_roles([{"type": "capture", "preset": "94"}], "hikvision")
    assert roles[0]["preset"] == ""
    roles = C._clean_roles([{"type": "capture", "preset": "12"}], "hikvision")
    assert roles[0]["preset"] == "12"


def test_fiche_preset_de_fonction_efface():
    f, err = C._device_fields({"channels": [{"no": 1, "roles": [{"type": "capture", "preset": "94"}]}]},
                              partial=True, old_brand="hikvision")
    assert err is None
    assert f["channels"][0]["roles"][0]["preset"] == ""


def test_capture_ia_ne_rappelle_pas_un_preset_de_fonction():
    capture_ia = pytest.importorskip("capture_ia")
    d = {"brand": "hikvision"}
    assert capture_ia._preset_of(d, {"preset": "94"}, "test") is None
    assert capture_ia._preset_of(d, {"preset": ""}, "test") is None
    assert capture_ia._preset_of(d, {"preset": "7"}, "test") == 7


# ---------------------------------------------------------------------------
# Usages
# ---------------------------------------------------------------------------
def test_un_seul_canal_lapi_par_appareil():
    chans = [{"no": 1, "roles": [{"type": "anpr"}]}, {"no": 2, "roles": [{"type": "anpr"}]}]
    f, err = C._device_fields({"channels": chans}, partial=True)
    assert f is None and "Un seul canal LAPI" in err
    f, err = C._device_fields({"channels": chans[:1]}, partial=True)
    assert err is None


def test_diff_journal():
    from bson.objectid import ObjectId
    pid = ObjectId()
    before = {"name": "A", "ip": "10.0.0.1", "credential_profile_id": None, "channels": [{"no": 1, "name": "x", "view": {"az": 1}}]}
    after = {"name": "B", "ip": "10.0.0.1", "credential_profile_id": pid, "channels": [{"no": 1, "name": "x", "view": {"az": 2}}]}
    d = C._diff(before, after, ["name", "ip", "credential_profile_id", "channels", "updatedAt"])
    # L'angle de vue n'est pas un champ de la fiche : pas de modification "canaux"
    assert d == {"name": ["A", "B"], "credential_profile_id": [None, str(pid)]}


# ---------------------------------------------------------------------------
# Capture manuelle
# ---------------------------------------------------------------------------
class FakeCam:
    def __init__(self, channel):
        self.channel = channel or 1

    def capture_image(self, path):
        with open(path, "wb") as f:
            f.write(b"jpeg-canal-%d" % self.channel)


@pytest.fixture
def capture_env(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(C, "make_hik_camera", lambda doc, ch=None: FakeCam(int(ch) if ch else None))
    monkeypatch.setattr(C, "_capture_busy", set())
    monkeypatch.setattr(C, "_capture_last", {})
    return tmp_path


DOC = {"_id": "a" * 24, "ip": "10.34.1.171", "name": "Test"}


def test_capture_vignette_du_bon_canal(capture_env):
    path, no = C.capture_to(DOC, 4)
    assert no == 4
    assert os.path.basename(path) == "%s_ch4_latest.jpg" % DOC["_id"]
    assert open(path, "rb").read() == b"jpeg-canal-4"
    # La vignette du canal 1 n'est pas touchee
    assert not os.path.exists(os.path.join(str(capture_env), "%s_latest.jpg" % DOC["_id"]))
    # Pas de fichier horodate ni temporaire
    assert sorted(os.listdir(str(capture_env))) == ["%s_ch4_latest.jpg" % DOC["_id"]]


def test_capture_vers_un_fichier_copie_la_vignette(capture_env, tmp_path_factory):
    dest = os.path.join(str(tmp_path_factory.mktemp("fiche")), "photo.jpg")
    C.capture_to(DOC, None, dest)
    assert open(dest, "rb").read() == b"jpeg-canal-1"
    assert open(os.path.join(str(capture_env), "%s_latest.jpg" % DOC["_id"]), "rb").read() == b"jpeg-canal-1"


def test_capture_delai_par_canal(capture_env):
    C.capture_to(DOC, 1)
    with pytest.raises(C.CaptureBusy):
        C.capture_to(DOC, 1)
    # Autre canal du meme appareil : sans attendre
    C.capture_to(DOC, 2)


def test_capture_appareil_occupe(capture_env):
    C._capture_busy.add(DOC["ip"])
    with pytest.raises(C.CaptureBusy):
        C.capture_to(DOC, 3)


def test_capture_libere_l_appareil_sur_erreur(capture_env, monkeypatch):
    class Boom(FakeCam):
        def capture_image(self, path):
            raise RuntimeError("injoignable")
    monkeypatch.setattr(C, "make_hik_camera", lambda doc, ch=None: Boom(ch))
    with pytest.raises(RuntimeError):
        C.capture_to(DOC, 1)
    assert DOC["ip"] not in C._capture_busy


def test_capture_en_echec_ne_laisse_rien(capture_env, monkeypatch):
    class Partiel(FakeCam):
        def capture_image(self, path):
            open(path, "wb").write(b"moitie")
            raise RuntimeError("coupure")
    monkeypatch.setattr(C, "make_hik_camera", lambda doc, ch=None: Partiel(ch))
    with pytest.raises(RuntimeError):
        C.capture_to(DOC, 1)
    assert os.listdir(str(capture_env)) == []
