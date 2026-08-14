using Toybox.WatchUi;
using Toybox.Graphics;

(:glance)
class CockpitGlanceView extends WatchUi.GlanceView {

    function initialize() {
        GlanceView.initialize();
    }

    function onUpdate(dc) {
        var st = Cache.load();
        var h = dc.getHeight();

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

        dc.setColor(color, Graphics.COLOR_TRANSPARENT);
        dc.drawText(0, (h * 3) / 4, Graphics.FONT_GLANCE,
                    "WBGT " + Fmt.wbgt(st != null ? st["w"] : null)
                        + "   alerte " + al.toString(),
                    Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER);
    }
}
