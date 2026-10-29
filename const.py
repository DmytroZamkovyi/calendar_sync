"""Constants of the Calendar Sync integration."""

DOMAIN = "calendar_sync"
SUBENTRY_PAIR = "pair"

# Keys of a pair subentry's data
CONF_CALENDAR_1 = "calendar_1"
CONF_CALENDAR_2 = "calendar_2"
CONF_DIRECTION_1TO2 = "direction_1to2"
CONF_DIRECTION_2TO1 = "direction_2to1"
CONF_CLOSED_MODE = "closed_mode"
CONF_PROPAGATE_DELETES = "propagate_deletes"
CONF_CONFLICT_POLICY = "conflict_policy"
CONF_DAYS_BACK = "days_back"
CONF_DAYS_FORWARD = "days_forward"
CONF_DRY_RUN = "dry_run"
CONF_RULES_1TO2 = "rules_1to2"
CONF_RULES_2TO1 = "rules_2to1"

# Option of the main entry
CONF_INTERVAL = "interval"

DEFAULT_INTERVAL = 10  # minutes
DEFAULT_DAYS_BACK = 7
DEFAULT_DAYS_FORWARD = 14
MAX_DAYS = 365
