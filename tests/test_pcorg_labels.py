import pcorg_labels as PL


def test_key_ignore_accents_casse_tirets():
    assert PL.key("Signalétique") == PL.key("signaletique")
    assert PL.key("Portail - Portillon") == PL.key("Portail-Portillon")
    assert PL.key("Electricité") != PL.key("Eclairage")


def test_same():
    assert PL.same("Clôture", "Cloture")
    assert PL.same(None, "")
    assert not PL.same("Cloture", None)
    assert PL.same(True, True) and not PL.same(True, False)


def test_best_prefere_accents_puis_frequence():
    assert PL.best(["Signaletique", "Signalétique"]) == "Signalétique"
    assert PL.best(["Depart de feux", "Depart De Feux"], {"Depart De Feux": 3}) == "Depart De Feux"
    assert PL.best(["A", "a"]) == "A"


def test_dedupe_items_garde_le_premier_id():
    items = [{"id": "1", "label": "Signaletique"}, {"id": "2", "label": "Vol"},
             {"id": "3", "label": "Signalétique"}]
    out, renamed = PL.dedupe_items(items)
    assert out == [{"id": "1", "label": "Signalétique"}, {"id": "2", "label": "Vol"}]
    assert renamed == {"Signaletique": "Signalétique"}


def test_canon_apply_cc():
    cfg = {"sous_classifications": {"PCO.Technique": [{"id": "1", "label": "Signaletique"},
                                                      {"id": "2", "label": "Signalétique"}]},
           "intervenants": [{"id": "a", "label": "Patrouille sécurité"}],
           "services": ["SDIS 72"]}
    c = PL.Canon(cfg)
    cc = {"sous_classification": "SIGNALETIQUE", "intervenant1": "Patrouille securite",
          "moyens_engages_niveau_1": ["patrouille securite", "Inconnu"],
          "service_contacte": "sdis 72", "texte": "Signaletique"}
    changed = c.apply_cc("PCO.Technique", cc)
    assert cc["sous_classification"] == "Signalétique"
    assert cc["intervenant1"] == "Patrouille sécurité"
    assert cc["moyens_engages_niveau_1"] == ["Patrouille sécurité", "Inconnu"]
    assert cc["service_contacte"] == "SDIS 72"
    assert cc["texte"] == "Signaletique"
    assert set(changed) == {"sous_classification", "intervenant1", "moyens_engages_niveau_1",
                            "service_contacte"}
    # Libelle inconnu ou autre categorie : inchange
    cc2 = {"sous_classification": "Nouveau"}
    assert PL.Canon(cfg).apply_cc("PCO.Secours", cc2) == [] and cc2["sous_classification"] == "Nouveau"
