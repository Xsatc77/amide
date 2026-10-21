"""Which section of the choices popup a metric belongs to, and which ones are on by default."""

SECTIONS: tuple[str, ...] = ("Activity", "Body", "Nutrition and water", "Sleep", "Heart and vitals", "Mind and other")

_GROUPS: dict[str, tuple[str, ...]] = {
    "Activity": ("step_count", "walking_running_distance", "flights_climbed", "active_energy", "basal_energy_burned", "apple_exercise_time",
                 "apple_stand_time", "apple_stand_hour", "walking_speed", "walking_step_length", "walking_asymmetry_percentage",
                 "walking_double_support_percentage", "stair_speed_up", "stair_speed_down", "cycling_distance", "swimming_distance",
                 "distance_downhill_snow_sports", "physical_effort"),
    "Body": ("weight_&_body_mass", "body_fat_percentage", "lean_body_mass", "body_mass_index", "height", "waist_circumference"),
    "Nutrition and water": ("dietary_water", "dietary_energy", "protein", "carbohydrates", "total_fat", "fiber", "dietary_sugar", "sodium",
                            "caffeine", "saturated_fat", "cholesterol", "potassium", "calcium", "iron"),
    "Sleep": ("sleep_analysis",),
    "Heart and vitals": ("heart_rate", "resting_heart_rate", "walking_heart_rate_average", "heart_rate_variability", "blood_pressure",
                         "blood_oxygen_saturation", "respiratory_rate", "body_temperature", "blood_glucose", "vo2_max",
                         "cardio_recovery", "apple_sleeping_wrist_temperature"),
}
_BY_NAME = {name: section for section, names in _GROUPS.items() for name in names}

# The default "on" list for a phone with no watch. Event kinds use the key "event:<kind>".
RECOMMENDED: frozenset[str] = frozenset({
    "step_count", "walking_running_distance", "flights_climbed", "weight_&_body_mass", "body_fat_percentage", "lean_body_mass",
    "body_mass_index", "dietary_water", "dietary_energy", "protein", "carbohydrates", "total_fat", "fiber",
    "event:symptom", "event:state_of_mind",
})


def metric_group(name: str) -> str:
    """The section a metric is listed under; a name nobody has mapped yet goes under "Mind and other"."""
    return _BY_NAME.get(name, "Mind and other")
