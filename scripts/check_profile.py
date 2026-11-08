#!/usr/bin/env python3
"""Check a company profile file offline: python scripts/check_profile.py path/to/profile.json

Prints one line per company, or every problem found. Exit code 1 when the file is not usable. No database or network needed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

from app import company_profiles  # noqa: E402


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    try:
        entries = company_profiles.load_file(argv[1])
    except company_profiles.ProfileError as e:
        print(f"{argv[1]} has {len(e.problems)} problem(s):")
        for p in e.problems:
            print(f"  - {p}")
        return 1
    print(f"{argv[1]} is usable: {len(entries)} company(ies)")
    for line in company_profiles.summary(entries):
        print(f"  {line}")
    print("Start the API with BIZ_COMPANY_PROFILES set to this path, then onboard with the CIN above.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
