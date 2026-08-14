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
        return [new CockpitView(), new CockpitDelegate()];
    }

    (:glance)
    function getGlanceView() {
        return [new CockpitGlanceView()];
    }

    function getServiceDelegate() {
        return [new CockpitService()];
    }
}
