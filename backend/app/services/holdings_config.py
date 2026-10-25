"""Thresholds the holdings services share, each defined once.

Kept apart from the services because they import one another (analytics imports the
service module, risk imports analytics, ...): a constant both ends need would otherwise
be defined twice, and two copies of a threshold drift apart.
"""

#: A held fund whose newest NAV trails the newest NAV of any held fund by up to this many
#: calendar days is still pending -- AMFI publishes some funds a day or two late -- and its
#: move for the missing days is still to come. Beyond it the fund is stale: merged, wound up
#: or suspended. Long enough for Diwali plus a weekend.
PENDING_NAV_DAYS = 7

#: The "stale NAV" insight and alert fire once a held fund has published nothing for this
#: many calendar days -- long past any holiday, so worth the owner's attention.
STALE_NAV_ALERT_DAYS = 30

#: Risk-free rate, % a year, for Sharpe-type ratios when the request gives none.
DEFAULT_RF_PCT = 6.5

#: Two NAVs this close (rupees) are the same NAV: AMCs and apps round the NAV they print,
#: so a tenth of a paisa either way is a match.
NAV_PRINT_TOLERANCE = 0.00015
