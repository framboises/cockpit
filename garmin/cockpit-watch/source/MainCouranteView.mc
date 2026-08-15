using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Time;

// Main courante : fiches PC Organisation en instance, par categorie, lues
// depuis Pages.bloc(Cache.loadPages(), "mc"). Aucun appel reseau propre a
// cette vue -- le bloc est deja rapatrie par Api.fetch/BgService (cf.
// Api.toPagesDict, Cache.savePages), CockpitView lit deja le meme bloc pour
// sa bande de voyants. La vue lit aussi Cache.load() pour savoir si on est
// hors evenement (mode "past"), independamment de ce que porte le bloc mc.
class MainCouranteView extends WatchUi.View {

    hidden var mIconeSecours;
    hidden var mIconeSecurite;
    hidden var mIconeTechnique;
    hidden var mIconeFlux;

    function initialize() {
        View.initialize();
        mIconeSecours = WatchUi.loadResource(Rez.Drawables.IconeSecours);
        mIconeSecurite = WatchUi.loadResource(Rez.Drawables.IconeSecurite);
        mIconeTechnique = WatchUi.loadResource(Rez.Drawables.IconeTechnique);
        mIconeFlux = WatchUi.loadResource(Rez.Drawables.IconeFlux);
    }

    // "3 (12)" -- en cours, puis terminees entre parentheses. `null` (bloc
    // absent) rend des tirets : ce n'est jamais un zero, qui se lirait comme
    // un calme operationnel avere.
    hidden function formatPaire(paire) {
        if (paire == null) {
            return "-- (--)";
        }
        return paire[0].toString() + " (" + paire[1].toString() + ")";
    }

    // Icone et texte partagent le meme sommet `y` (les deux sont ancres par
    // le haut, comme partout ailleurs dans l'app).
    hidden function ligneCategorie(dc, y, iconeX, texteX, icone, texte) {
        dc.drawBitmap(iconeX, y, icone);
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(texteX, y, Graphics.FONT_SMALL, texte,
                    Graphics.TEXT_JUSTIFY_LEFT);
    }

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();

        var w = dc.getWidth();
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var hS = dc.getFontHeight(Graphics.FONT_SMALL);

        // Titre : moitie haute, sommet contraint -- ecran mesure au device
        // (fenix8solar51mm, 280x280), corde tres au-dessus du besoin du
        // libelle.
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 26, Graphics.FONT_XTINY, "MAIN COURANTE",
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Mode "past" (hors evenement) prime sur tout : aucune fiche PC org
        // n'a de sens a afficher hors saison, et un "0 (0)" par categorie
        // laisserait croire a un calme operationnel qui ne dit rien.
        if (State.isPast(Cache.load())) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, dc.getHeight() / 2 - hS / 2, Graphics.FONT_SMALL,
                        "hors evenement", Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }

        var mc = Pages.bloc(Cache.loadPages(), "mc");

        // Premiere ligne icone, moitie haute : c'est son sommet qui
        // contraint. C'est aussi la plus etroite des quatre lignes icone
        // (elle est la plus proche du bord) -- la colonne icone/texte est
        // donc fixee depuis SA corde, ce qui la garde alignee sur les
        // quatre lignes plutot que de la faire onduler comme le ferait un
        // recalcul par ligne.
        //
        // Cinq lignes (quatre categories + le repli "+ N autres") doivent
        // tenir entre le titre et le pied (241 sur un ecran de 280, mesure
        // au device) : l'espacement d'origine (gap 14 puis 8 entre lignes)
        // debordait sur le pied de 23 px (mesure a la sonde). Resserre a
        // gap 10 puis 2 -- verifie par DebordementTest.mc, pire cas
        // (compteurs a deux chiffres partout) : la derniere ligne finit a
        // 236, 5 px avant le pied.
        var y = 26 + hX + 10;
        var chordEtroite = Pages.largeurUtile(dc, y, hS);
        var margeCol = (w - chordEtroite) / 2.0 + 6;
        var iconeX = margeCol;
        var texteX = iconeX + 32 + 10;

        ligneCategorie(dc, y, iconeX, texteX, mIconeSecours,
                       formatPaire(mc != null ? mc["s"] : null));
        y += hS + 2;
        ligneCategorie(dc, y, iconeX, texteX, mIconeSecurite,
                       formatPaire(mc != null ? mc["sc"] : null));
        y += hS + 2;
        ligneCategorie(dc, y, iconeX, texteX, mIconeTechnique,
                       formatPaire(mc != null ? mc["tq"] : null));
        y += hS + 2;
        ligneCategorie(dc, y, iconeX, texteX, mIconeFlux,
                       formatPaire(mc != null ? mc["f"] : null));
        y += hS + 2;

        // Ligne repliee : Information + Main Courante + Fourriere. Les
        // taire ferait disparaitre des fiches reelles de l'ecran.
        var texteAutres = "+ " + formatPaire(mc != null ? mc["o"] : null);
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_SMALL, texteAutres,
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Pied de page : age du bloc, ou son absence nommee explicitement --
        // jamais un silence qui laisserait croire que tout va bien.
        var yFoot = dc.getHeight() - hX - 17;
        if (mc == null) {
            dc.setColor(Graphics.COLOR_RED, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, yFoot, Graphics.FONT_XTINY, "indisponible",
                        Graphics.TEXT_JUSTIFY_CENTER);
        } else {
            var now = Time.now().value();
            var age = (mc["t"] != null) ? (now - mc["t"]) : null;
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, yFoot, Graphics.FONT_XTINY,
                        "maj " + Fmt.age(age), Graphics.TEXT_JUSTIFY_CENTER);
        }
    }
}
