using Toybox.WatchUi;
using Toybox.Graphics;

class CockpitView extends WatchUi.View {

    function initialize() {
        View.initialize();
    }

    function onLayout(dc) {
    }

    function onShow() {
    }

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        dc.drawText(dc.getWidth() / 2, dc.getHeight() / 2,
                    Graphics.FONT_MEDIUM, "Cockpit",
                    Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
    }

    function onHide() {
    }
}
