"""US or metric display units. Everything is stored in US units (pounds, inches, ounces, mph) and converted at the edges:
numbers are converted when they are typed in and again when they are shown, so switching the setting never touches stored data.

A template gets `u` (see `Units`): `u.weight(lbs)`, `u.length(inches)`, `u.volume(oz)`, `u.speed(mph)` give the number to show,
`u.weight_label` etc. the unit name, and `u.weight_in(typed)` etc. turn what was typed back into US units."""

LB_PER_KG = 2.2046226218
CM_PER_IN = 2.54
ML_PER_OZ = 29.5735295625
KMH_PER_MPH = 1.609344

US, METRIC = "us", "metric"
CHOICES = ((US, "US (lb, in, oz, mph)"), (METRIC, "Metric (kg, cm, mL, km/h)"))


class Units:
    def __init__(self, system: str | None = US):
        self.system = METRIC if system == METRIC else US
        self.metric = self.system == METRIC
        self.weight_label = "kg" if self.metric else "lbs"
        self.length_label = "cm" if self.metric else "in"
        self.volume_label = "mL" if self.metric else "oz"
        self.speed_label = "km/h" if self.metric else "mph"
        self.load_label = "kg" if self.metric else "lb"

    # US -> display
    def weight(self, lbs, places: int = 1):
        return self._out(lbs, 1 / LB_PER_KG, places)

    def length(self, inches, places: int = 1):
        return self._out(inches, CM_PER_IN, places)

    def volume(self, oz, places: int = -1):       # to the nearest 10 mL: a whole-ounce goal is not a precise number of mL
        return self._out(oz, ML_PER_OZ, places, whole=True)

    def speed(self, mph, places: int = 1):
        return self._out(mph, KMH_PER_MPH, places)

    # typed -> US
    def weight_in(self, value):
        return self._in(value, LB_PER_KG)

    def length_in(self, value):
        return self._in(value, 1 / CM_PER_IN)

    def volume_in(self, value):
        return self._in(value, 1 / ML_PER_OZ)

    def speed_in(self, value):
        return self._in(value, 1 / KMH_PER_MPH)

    def _out(self, value, factor, places, whole=False):
        if value is None or not self.metric:
            return value
        out = round(value * factor, places)
        return int(out) if whole or (places == 0) else out

    def _in(self, value, factor):
        if value is None or not self.metric:
            return value
        return value * factor


def for_user(user) -> Units:
    return Units(getattr(user, "units", None))
