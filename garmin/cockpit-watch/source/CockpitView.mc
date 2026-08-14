using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Application;
using Toybox.Timer;
using Toybox.Time;

class CockpitView extends WatchUi.View {

    hidden var mState = null;
    hidden var mTimer = null;
    hidden var mPage = 0;

    function initialize() {
        View.initialize();
    }

    function onLayout(dc) {
    }

    function onShow() {
        refresh();
        mTimer = new Timer.Timer();
        mTimer.start(method(:onTick), periodMs(), true);
    }

    function onHide() {
        if (mTimer != null) {
            mTimer.stop();
            mTimer = null;
        }
    }

    function onTick() as Void {
        refresh();
    }

    function periodMs() {
        var peak = Application.Properties.getValue("pollPeak");
        var normal = Application.Properties.getValue("pollNormal");
        if (peak == null) { peak = 60; }
        if (normal == null) { normal = 180; }
        // On resserre le rythme des que ca chauffe, sans reglage manuel.
        var level = State.alertMax(mState);
        var wl = State.wbgtLevel(mState);
        if (level >= 2 || wl >= 2) {
            return peak * 1000;
        }
        return normal * 1000;
    }

    function refresh() {
        var mock = Application.Properties.getValue("mockData");
        if (mock != null && mock) {
            var scenario = Application.Properties.getValue("mockScenario");
            if (scenario == null) { scenario = 0; }
            Cache.save(Mock.state(scenario, Time.now().value()));
        }
        mState = Cache.load();
        WatchUi.requestUpdate();
    }

    function nextPage() {
        mPage = (mPage + 1) % 2;
        WatchUi.requestUpdate();
    }

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        if (mPage == 0) {
            drawMain(dc);
        } else {
            drawAlerts(dc);
        }
    }

    hidden function levelColor(level) {
        if (level >= 3) { return Graphics.COLOR_RED; }
        if (level >= 2) { return Graphics.COLOR_ORANGE; }
        if (level >= 1) { return Graphics.COLOR_YELLOW; }
        return Graphics.COLOR_GREEN;
    }

    hidden function drawMain(dc) {
        var w = dc.getWidth();
        var st = mState;
        var now = Time.now().value();

        // Evenement rapporte, en haut : sans lui, une configuration epinglee
        // sur le mauvais evenement serait invisible.
        var label = (st != null && st["n"] != null) ? st["n"] : "--";
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 26, Graphics.FONT_XTINY, label,
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Entrees, le chiffre principal.
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 58, Graphics.FONT_NUMBER_MEDIUM,
                    Fmt.count(st != null ? st["e"] : null),
                    Graphics.TEXT_JUSTIFY_CENTER);
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 108, Graphics.FONT_XTINY,
                    Fmt.rate(st != null ? st["er"] : null) + " pers/h",
                    Graphics.TEXT_JUSTIFY_CENTER);

        // WBGT, colore par son niveau.
        var wl = State.wbgtLevel(st);
        dc.setColor(levelColor(wl), Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 136, Graphics.FONT_MEDIUM,
                    "WBGT " + Fmt.wbgt(st != null ? st["w"] : null),
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Alertes : trois lignes au plus, le reste sur la seconde page.
        var y = 176;
        var al = (st != null && st["al"] != null) ? st["al"] : [];
        if (al.size() == 0) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, "RAS",
                        Graphics.TEXT_JUSTIFY_CENTER);
        } else {
            var n = al.size() > 3 ? 3 : al.size();
            for (var i = 0; i < n; i += 1) {
                dc.setColor(levelColor(al[i][0]), Graphics.COLOR_TRANSPARENT);
                dc.drawText(w / 2, y, Graphics.FONT_XTINY, al[i][1],
                            Graphics.TEXT_JUSTIFY_CENTER);
                y += 18;
            }
        }

        // Age de la donnee, en rouge des qu'elle est perimee.
        var staleAfter = Application.Properties.getValue("staleAfter");
        if (staleAfter == null) { staleAfter = 90; }
        var age = State.worstAgeSec(st, now);
        var stale = State.isStale(st, now, staleAfter);
        dc.setColor(stale ? Graphics.COLOR_RED : Graphics.COLOR_DK_GRAY,
                    Graphics.COLOR_TRANSPARENT);
        var foot = stale ? ("perime depuis " + Fmt.age(age)) : Fmt.age(age);
        dc.drawText(w / 2, 244, Graphics.FONT_XTINY, foot,
                    Graphics.TEXT_JUSTIFY_CENTER);
    }

    hidden function drawAlerts(dc) {
        var w = dc.getWidth();
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 30, Graphics.FONT_XTINY, "ALERTES",
                    Graphics.TEXT_JUSTIFY_CENTER);
        var al = (mState != null && mState["al"] != null) ? mState["al"] : [];
        if (al.size() == 0) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, 130, Graphics.FONT_SMALL, "RAS",
                        Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }
        var y = 66;
        for (var i = 0; i < al.size(); i += 1) {
            dc.setColor(levelColor(al[i][0]), Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, al[i][1],
                        Graphics.TEXT_JUSTIFY_CENTER);
            y += 30;
        }
    }
}
