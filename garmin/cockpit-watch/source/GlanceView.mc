using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Application;
using Toybox.Time;

(:glance)
class CockpitGlanceView extends WatchUi.GlanceView {

    function initialize() {
        GlanceView.initialize();
    }

    function onUpdate(dc) {
        var st = Cache.load();
        var h = dc.getHeight();
        var now = Time.now().value();

        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(0, h / 4, Graphics.FONT_GLANCE,
                    Fmt.count(st != null ? st["e"] : null),
                    Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER);

        var al = State.alertMax(st);
        var color = Graphics.COLOR_GREEN;
        var worst = State.worstLevel(st);
        if (worst >= 3) { color = Graphics.COLOR_RED; }
        else if (worst >= 2) { color = Graphics.COLOR_ORANGE; }
        else if (worst >= 1) { color = Graphics.COLOR_YELLOW; }

        // La glance est la surface principale du mode 24h (spec 4.6, glance
        // + service de fond) et n'affichait jusqu'ici aucun age : un
        // telephone hors de portee depuis six heures, un jeton revoque ou un
        // certificat expire montrait le meme compteur que si la donnee
        // venait d'arriver. Au-dela de staleAfter, la seconde ligne remplace
        // le compte d'alertes par l'age -- mesure sur le device
        // (buffer hors-ecran, Graphics.createBufferedBitmap) : la ligne
        // complete avec l'age tient sous les 217 px de la zone de glance
        // meme au pire cas a deux chiffres d'heures (207 px mesures), alors
        // qu'ajouter l'age a la ligne actuelle (180 px mesures) ne le
        // pouvait pas (37 px restants). La couleur de la bande (pas de
        // cette ligne) est traitee a part dans getGlanceTheme.
        var staleAfter = Application.Properties.getValue("staleAfter");
        if (staleAfter == null) { staleAfter = 90; }
        var stale = State.isStale(st, now, staleAfter);

        var ligne2 = "WBGT " + Fmt.wbgt(st != null ? st["w"] : null);
        if (stale) {
            ligne2 += " perime " + Fmt.age(State.worstAgeSec(st, now));
        } else {
            ligne2 += "   alerte " + al.toString();
        }

        dc.setColor(color, Graphics.COLOR_TRANSPARENT);
        dc.drawText(0, (h * 3) / 4, Graphics.FONT_GLANCE, ligne2,
                    Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER);
    }
}
