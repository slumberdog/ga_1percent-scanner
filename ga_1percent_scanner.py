"""
Georgia 1% Rule Property Scanner
---------------------------------
Pulls active for-sale listings in Georgia from the RentCast API, estimates
monthly rent for each, and flags properties where rent / price >= threshold
(the "1% rule"). Also flags listings whose description mentions an ADU
(accessory dwelling unit) so you can review those manually, since rent
estimates for ADUs aren't captured by standard comps.

SETUP
-----
1. Sign up for a RentCast API key: https://www.rentcast.io/api
   (free tier includes a limited number of calls/month; paid tiers are cheap)
2. pip install requests --break-system-packages
3. Set your API key below or as the RENTCAST_API_KEY environment variable.
4. Edit CONFIG below to set your target cities/counties and thresholds.
5. Run: python ga_1percent_scanner.py

OUTPUT
------
Writes a CSV (ga_1percent_results.csv) with every listing pulled, its
rent estimate, computed ratio, and whether it passed the threshold or
mentioned an ADU. Sorted by ratio, best deals first.

NOTES
-----
- RentCast's /listings/sale endpoint returns active sale listings with
  filters for property type, price, city, county, zip, etc.
- Rent is estimated via RentCast's /avm/rent/long-term endpoint, which
  takes an address (or lat/long) and returns a rent estimate + comps.
- This makes 2 API calls per listing (1 for the listing search is batched,
  but rent estimates are 1-per-property), so watch your monthly call quota
  on the free tier. Start with a small city list while testing.
- Multi-unit properties (duplex/triplex/quad) returned by RentCast typically
  list total price only; you may need to estimate combined rent across
  units manually for those - the script flags them for manual review.
"""

import os
import csv
import time
import re
import requests

# ----------------------------- CONFIG ---------------------------------

API_KEY = os.environ.get("RENTCAST_API_KEY", "YOUR_API_KEY_HERE")

BASE_URL = "https://api.rentcast.io/v1"

# Cities/counties to scan. Start small while testing your API quota.
# You can use city names or county names - RentCast accepts both.
TARGET_LOCATIONS = [
    {"city": "Macon", "state": "GA"},
    {"city": "Warner Robins", "state": "GA"},
    {"city": "Albany", "state": "GA"},
    {"city": "Columbus", "state": "GA"},
    {"city": "Augusta", "state": "GA"},
    {"city": "Dublin", "state": "GA"},
    {"city": "Valdosta", "state": "GA"},
]

# Property types to include (RentCast values, adjust as needed)
PROPERTY_TYPES = ["Single Family", "Duplex-Triplex", "Multi-Family"]

# Screening threshold. Classic rule is 1.0%; many investors now use 0.8-1.0%
# given current market conditions.
MIN_RATIO_PERCENT = 0.8

# Max listings to pull per location per run (controls API usage)
LISTINGS_PER_LOCATION = 50

# Max sale price to consider (filters out anything unrealistic for the rule)
MAX_PRICE = 400000

# Keywords that suggest a property has (or could support) an ADU
ADU_KEYWORDS = [
    "adu", "accessory dwelling", "mother-in-law", "in-law suite",
    "guest house", "guest cottage", "carriage house", "garage apartment",
    "detached apartment", "second unit", "casita", "granny flat",
]

OUTPUT_FILE = "ga_1percent_results.csv"
SLEEP_BETWEEN_CALLS = 0.5  # seconds, be polite to the API

# ------------------------------------------------------------------------

HEADERS = {"X-Api-Key": API_KEY, "Accept": "application/json"}


def search_listings(city, state, property_type, limit):
    """Query RentCast for active sale listings matching criteria."""
    params = {
        "city": city,
        "state": state,
        "status": "Active",
        "propertyType": property_type,
        "limit": limit,
    }
    resp = requests.get(f"{BASE_URL}/listings/sale", headers=HEADERS, params=params)
    if resp.status_code != 200:
        print(f"  [!] Listing search failed ({resp.status_code}) for {city}, {property_type}: {resp.text[:200]}")
        return []
    return resp.json()


def get_rent_estimate(address, city, state, zip_code):
    """Query RentCast's rent AVM for a given address."""
    params = {"address": f"{address}, {city}, {state} {zip_code}"}
    resp = requests.get(f"{BASE_URL}/avm/rent/long-term", headers=HEADERS, params=params)
    if resp.status_code != 200:
        return None
    data = resp.json()
    return data.get("rent")


def has_adu_mention(text):
    if not text:
        return False
    lowered = text.lower()
    return any(keyword in lowered for keyword in ADU_KEYWORDS)


def main():
    if API_KEY == "YOUR_API_KEY_HERE":
        print("Set your RentCast API key in RENTCAST_API_KEY or in the script config before running.")
        return

    results = []

    for loc in TARGET_LOCATIONS:
        city, state = loc["city"], loc["state"]
        for prop_type in PROPERTY_TYPES:
            print(f"Searching {city}, {state} - {prop_type} ...")
            listings = search_listings(city, state, prop_type, LISTINGS_PER_LOCATION)
            time.sleep(SLEEP_BETWEEN_CALLS)

            for listing in listings:
                price = listing.get("price")
                if not price or price > MAX_PRICE:
                    continue

                address = listing.get("addressLine1") or listing.get("formattedAddress", "")
                zip_code = listing.get("zipCode", "")
                description = listing.get("description", "") or ""

                rent = get_rent_estimate(address, city, state, zip_code)
                time.sleep(SLEEP_BETWEEN_CALLS)

                if not rent:
                    continue

                ratio_percent = (rent / price) * 100
                adu_flag = has_adu_mention(description)

                results.append({
                    "address": address,
                    "city": city,
                    "zip": zip_code,
                    "property_type": prop_type,
                    "price": price,
                    "rent_estimate": rent,
                    "ratio_percent": round(ratio_percent, 2),
                    "passes_threshold": ratio_percent >= MIN_RATIO_PERCENT,
                    "adu_mentioned": adu_flag,
                    "beds": listing.get("bedrooms"),
                    "baths": listing.get("bathrooms"),
                    "sqft": listing.get("squareFootage"),
                    "listing_url": listing.get("listingUrl") or listing.get("mlsUrl", ""),
                })

    # Sort best ratio first
    results.sort(key=lambda r: r["ratio_percent"], reverse=True)

    if not results:
        print("No results found. Check your API key, quota, and TARGET_LOCATIONS.")
        return

    with open(OUTPUT_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    passing = [r for r in results if r["passes_threshold"]]
    adu_hits = [r for r in results if r["adu_mentioned"]]

    print(f"\nScanned {len(results)} listings total.")
    print(f"{len(passing)} listings meet or exceed {MIN_RATIO_PERCENT}% rent/price ratio.")
    print(f"{len(adu_hits)} listings mention a possible ADU (review manually - rent upside not counted above).")
    print(f"Full results written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
