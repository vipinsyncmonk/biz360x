"""Test configuration. Set before the app is imported: the suite makes far more than 600 requests a minute as one user, so the
per-minute limits are lifted here and the rate-limit tests set their own."""
import os
import time

os.environ.setdefault("BIZ_RATE_USER", "1000000")
os.environ.setdefault("BIZ_RATE_HEAVY", "1000000")
os.environ.setdefault("BIZ_RATE_SIGNUP", "1000000")

# The database counts days in UTC (current_date). Python's date.today() uses the machine's clock zone, so on a machine in India the two
# disagree between 00:00 and 05:30 and tests that compare them fail for those hours. Pin the process to UTC so both mean the same day.
os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()
