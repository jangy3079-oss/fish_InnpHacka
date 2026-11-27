"""R7 camera observation hold, independent per cage and configuration (#152)."""

from datetime import timedelta


class FeedingRule:
    def __init__(self):
        self.last = {}
        self.confirmed = {}

    def evaluate(self,cage,ts,eligible,feeding,quality,rule,thresholds):
        cycle = timedelta(minutes=thresholds['cycle_minutes'])
        gap = cage in self.last and ts-self.last[cage]!=cycle
        self.last[cage] = ts
        if not eligible or gap:
            self.confirmed.pop(cage,None)
        observed = feeding is not None and quality is not None and quality>=thresholds['quality']['camera_only_min']
        if eligible and observed:
            if feeding<=rule['feeding_change_max']:
                self.confirmed[cage] = (ts,feeding)
            else:
                self.confirmed.pop(cage,None)
        previous = self.confirmed.get(cage)
        if previous and ts-previous[0]>timedelta(hours=rule['max_hold_hours']):
            self.confirmed.pop(cage,None)
            previous = None
        matched = eligible and previous is not None
        return matched, {'feeding_current_observed':observed,'feeding_held_due_to_missing':bool(matched and not observed),
            'feeding_last_observed_at':previous[0].isoformat() if previous else None,
            'feeding_change_last_observed':previous[1] if previous else None}
