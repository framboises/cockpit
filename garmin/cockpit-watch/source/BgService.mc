using Toybox.System;
using Toybox.Background;

(:background)
class CockpitService extends System.ServiceDelegate {

    function initialize() {
        ServiceDelegate.initialize();
    }

    function onTemporalEvent() {
        Api.fetch(method(:onFetched));
    }

    function onFetched(ok, st) {
        if (ok && st != null) {
            Cache.save(st);
            Alerting.check(st);
        }
        Background.exit(null);
    }
}
