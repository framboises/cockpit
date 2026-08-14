using Toybox.Application;
using Toybox.WatchUi;
using Toybox.Background;
using Toybox.Time;

class CockpitApp extends Application.AppBase {

    function initialize() {
        AppBase.initialize();
    }

    function onStart(state) {
        // 5 minutes est le plancher impose par la plateforme, pas un choix :
        // "Temporal events cannot be set to occur less than 5 minutes after
        // the last temporal event occurred". Un seul evenement temporel peut
        // etre enregistre a la fois.
        if (Toybox has :Background) {
            Background.registerForTemporalEvent(new Time.Duration(300));
        }
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
    //
    // La fraicheur passe avant le niveau : une donnee perimee (telephone
    // hors de portee, jeton revoque, certificat expire) rend le niveau
    // affiche non fiable -- une bande verte dessus etait un mensonge, pas
    // une absence d'alerte. `staleAfter` reprend le meme repli a 90 s que
    // CockpitView et GlanceView.
    (:glance)
    function getGlanceTheme() {
        var st = Cache.load();
        var staleAfter = Application.Properties.getValue("staleAfter");
        if (staleAfter == null) { staleAfter = 90; }
        if (State.isStale(st, Time.now().value(), staleAfter)) {
            return AppBase.GLANCE_THEME_GOLD;
        }
        var worst = State.worstLevel(st);
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
