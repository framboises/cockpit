using Toybox.System;
using Toybox.Background;

(:background)
class CockpitService extends System.ServiceDelegate {

    function initialize() {
        ServiceDelegate.initialize();
    }

    function onTemporalEvent() {
        Background.exit(null);
    }
}
