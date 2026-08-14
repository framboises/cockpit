using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Application;
using Toybox.Timer;
using Toybox.Time;

class CockpitView extends WatchUi.View {

    hidden var mState = null;
    hidden var mTimer = null;
    hidden var mPage = 0;
    hidden var mPeriod = 0;

    function initialize() {
        View.initialize();
    }

    function onLayout(dc) {
    }

    function onShow() {
        refresh();
        mPeriod = periodMs();
        mTimer = new Timer.Timer();
        mTimer.start(method(:onTick), mPeriod, true);
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
        // Reajuste le rythme si le niveau d'alerte ou le WBGT a franchi le
        // seuil pendant que la vue est affichee : sans ca, le polling reste
        // bloque sur la valeur armee a l'ouverture de la vue.
        if (mTimer != null) {
            var p = periodMs();
            if (p != mPeriod) {
                mTimer.stop();
                mTimer.start(method(:onTick), p, true);
                mPeriod = p;
            }
        }
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

        // drawText ancre par le haut (pas de TEXT_JUSTIFY_VCENTER) : un bloc
        // pose a y occupe y -> y + hauteur police. Les positions sont donc
        // calculees a partir des hauteurs reelles du device, pas figees en
        // dur, pour survivre a un changement de police ou de device.
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var hM = dc.getFontHeight(Graphics.FONT_MEDIUM);
        var hN = dc.getFontHeight(Graphics.FONT_NUMBER_MEDIUM);

        var al = (st != null && st["al"] != null) ? st["al"] : [];

        // Evenement rapporte, en haut : sans lui, une configuration epinglee
        // sur le mauvais evenement serait invisible. Le compte d'alertes y
        // est accroche pour rester visible malgre la troncature a deux
        // lignes plus bas.
        var label = (st != null && st["n"] != null) ? st["n"] : "--";
        if (al.size() == 1) {
            label = label + " · 1 alerte";
        } else if (al.size() > 1) {
            label = label + " · " + al.size().toString() + " alertes";
        }
        var y = 24;
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_XTINY, label,
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Entrees, le chiffre principal.
        y = y + hX + 4;
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_NUMBER_MEDIUM,
                    Fmt.count(st != null ? st["e"] : null),
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Debit.
        y = y + hN + 2;
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_XTINY,
                    Fmt.rate(st != null ? st["er"] : null) + " pers/h",
                    Graphics.TEXT_JUSTIFY_CENTER);

        // WBGT, colore par son niveau.
        y = y + hX + 5;
        var wl = State.wbgtLevel(st);
        dc.setColor(levelColor(wl), Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_MEDIUM,
                    "WBGT " + Fmt.wbgt(st != null ? st["w"] : null),
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Alertes : deux lignes au plus, le reste sur la seconde page.
        y = y + hM + 4;
        if (al.size() == 0) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, "RAS",
                        Graphics.TEXT_JUSTIFY_CENTER);
        } else {
            var n = al.size() > 2 ? 2 : al.size();
            for (var i = 0; i < n; i += 1) {
                dc.setColor(levelColor(al[i][0]), Graphics.COLOR_TRANSPARENT);
                dc.drawText(w / 2, y, Graphics.FONT_XTINY, al[i][1],
                            Graphics.TEXT_JUSTIFY_CENTER);
                y += hX + 2;
            }
        }

        // Age de la donnee, en rouge des qu'elle est perimee. Positionne
        // depuis le bas de l'ecran (rond : la corde disponible se retrecit
        // pres du bord, d'ou le texte raccourci sans "depuis").
        var staleAfter = Application.Properties.getValue("staleAfter");
        if (staleAfter == null) { staleAfter = 90; }
        var age = State.worstAgeSec(st, now);
        var stale = State.isStale(st, now, staleAfter);
        dc.setColor(stale ? Graphics.COLOR_RED : Graphics.COLOR_DK_GRAY,
                    Graphics.COLOR_TRANSPARENT);
        var foot = stale ? ("perime " + Fmt.age(age)) : Fmt.age(age);
        dc.drawText(w / 2, dc.getHeight() - hX - 17, Graphics.FONT_XTINY, foot,
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
