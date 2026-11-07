"""Test configuration. Set before the app is imported: the suite makes far more than 600 requests a minute as one user, so the
per-minute limits are lifted here and the rate-limit tests set their own."""
import os

os.environ.setdefault("BIZ_RATE_USER", "1000000")
os.environ.setdefault("BIZ_RATE_HEAVY", "1000000")
