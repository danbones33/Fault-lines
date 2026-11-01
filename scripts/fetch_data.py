"""
Optional: refresh data/states.json from live public APIs.

  Census ACS 1-year (median household income B19013_001E, median gross rent B25064_001E)
  BLS LAUS state unemployment (seasonally adjusted, latest month)

No API key needed for Census at this volume; BLS v1 allows a few hundred
unregistered calls a day. Network failures leave the file untouched.

    python -I scripts/fetch_data.py [--acs-year 2023]
"""
import argparse
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data", "states.json")
FIPS = {"01":"AL","02":"AK","04":"AZ","05":"AR","06":"CA","08":"CO","09":"CT","10":"DE","12":"FL","13":"GA","15":"HI","16":"ID",
        "17":"IL","18":"IN","19":"IA","20":"KS","21":"KY","22":"LA","23":"ME","24":"MD","25":"MA","26":"MI","27":"MN","28":"MS",
        "29":"MO","30":"MT","31":"NE","32":"NV","33":"NH","34":"NJ","35":"NM","36":"NY","37":"NC","38":"ND","39":"OH","40":"OK",
        "41":"OR","42":"PA","44":"RI","45":"SC","46":"SD","47":"TN","48":"TX","49":"UT","50":"VT","51":"VA","53":"WA","54":"WV",
        "55":"WI","56":"WY"}


def get_json(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def acs(year):
    url = f"https://api.census.gov/data/{year}/acs/acs1?get=NAME,B19013_001E,B25064_001E&for=state:*"
    rows = get_json(url)
    out = {}
    for name, inc, rent, fips in rows[1:]:
        if fips in FIPS:
            out[FIPS[fips]] = {"median_income": int(inc), "median_rent": int(rent)}
    return out


def bls():
    series = [f"LASST{f}0000000000003" for f in FIPS]  # unemployment rate, SA
    out = {}
    for i in range(0, len(series), 25):
        body = json.dumps({"seriesid": series[i:i + 25], "latest": True}).encode()
        res = get_json("https://api.bls.gov/publicAPI/v1/timeseries/data/", body, {"Content-Type": "application/json"})
        for s in res.get("Results", {}).get("series", []):
            fips = s["seriesID"][5:7]
            if s.get("data"):
                out[FIPS[fips]] = float(s["data"][0]["value"])
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--acs-year", type=int, default=2023)
    args = ap.parse_args()
    with open(DATA, encoding="utf-8") as f:
        doc = json.load(f)
    try:
        a = acs(args.acs_year)
        print(f"ACS {args.acs_year}: {len(a)} states")
    except Exception as e:
        print("ACS failed:", e); a = {}
    try:
        b = bls()
        print(f"BLS LAUS: {len(b)} states")
    except Exception as e:
        print("BLS failed:", e); b = {}
    n = 0
    for s in doc["states"]:
        c = s["code"]
        if c in a:
            s.update(a[c]); n += 1
        if c in b:
            s["unemployment_pct"] = b[c]
        s["rent_burden_pct"] = round(s["median_rent"] * 12 / s["median_income"] * 100, 1)
    if n or b:
        doc["_note"] = f"Refreshed from Census ACS {args.acs_year} / BLS LAUS by scripts/fetch_data.py. Policy pressures remain illustrative."
        with open(DATA, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        print("updated", DATA)
    else:
        print("nothing updated")
        sys.exit(1)
