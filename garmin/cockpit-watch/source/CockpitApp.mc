using Toybox.Application;
using Toybox.WatchUi;

class CockpitApp extends Application.AppBase {

    function initialize() {
        AppBase.initialize();
    }

    function onStart(state) {
    }

    function onStop(state) {
    }

    function getInitialView() {
        var view = new CockpitView();
        return [view, new CockpitDelegate(view)];
    }

    (:glance)
    function getGlanceView() {
        return [new CockpitGlanceView()];
    }

    // La bande verticale que le systeme dessine a gauche de la glance. La
    // colorer coute zero pixel dessine et donne le niveau au premier regard.
    (:glance)
    function getGlanceTheme() {
        var st = Cache.load();
        var wl = State.wbgtLevel(st);
        var al = State.alertMax(st);
        var worst = wl > al ? wl : al;
        if (worst >= 3) {
            return AppBase.GLANCE_THEME_RED;
        }
        if (worst >= 2) {
            return AppBase.GLANCE_THEME_GOLD;
        }
        return AppBase.GLANCE_THEME_GREEN;
    }

    function getServiceDelegate() {
        return [new CockpitService()];
    }
}
