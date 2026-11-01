"""
Builds data/states.json: one record per US state, with the grievance /
economic inputs that drive each agent's persona.

Values are APPROXIMATE, hand-entered from public BLS LAUS (unemployment,
mid-2025) and Census ACS 2023 1-year (median household income, median
gross rent). Good enough for a persona prompt, not for analysis.
Run scripts/fetch_data.py to refresh from the live APIs.

Columns per tuple:
  code, name, region, unemployment_pct, median_income, median_rent,
  federal_funds_share_pct (approx. share of state revenue from federal
  transfers), key_industries, policy_pressures (illustrative, economic /
  policy only -- never demographic)
"""
import json, os

S = [
 ("AL","Alabama","Deep South",3.3,62000,1000,41,["auto manufacturing","aerospace","poultry"],["Medicaid expansion stalemate","rural hospital closures","grocery tax fight"]),
 ("AK","Alaska","Pacific",4.7,89000,1350,38,["oil & gas","fishing","federal/military"],["Permanent Fund dividend cuts","ferry service cuts","oil revenue volatility"]),
 ("AZ","Arizona","Southwest",4.1,77000,1500,38,["semiconductors","construction","tourism"],["Colorado River water cuts","housing cost surge","school voucher budget hole"]),
 ("AR","Arkansas","South",3.7,58000,900,36,["retail HQ","agriculture","trucking"],["low wages vs rising rent","rural broadband gaps","Medicaid work requirements"]),
 ("CA","California","West Coast",5.5,96000,2000,32,["tech","entertainment","agriculture","ports"],["home insurance market collapse","housing affordability crisis","state budget deficit","wildfire recovery"]),
 ("CO","Colorado","Mountain West",4.2,93000,1700,28,["aerospace","outdoor recreation","energy"],["property tax spike","water scarcity","housing costs in mountain towns"]),
 ("CT","Connecticut","Northeast",3.6,93000,1450,27,["insurance","defense manufacturing","finance"],["electric rates among highest in US","pension burden","cost of living squeeze"]),
 ("DE","Delaware","Mid-Atlantic",3.9,82000,1400,30,["corporate services","chemicals","banking"],["coastal flooding costs","hospital cost review fight","state budget shortfall"]),
 ("FL","Florida","South",3.7,73000,1650,33,["tourism","real estate","agriculture","aerospace"],["homeowners insurance crisis","condo assessment shock","property tax repeal push","hurricane recovery"]),
 ("GA","Georgia","South",3.5,75000,1350,33,["logistics","film","EV manufacturing","agriculture"],["Medicaid expansion refusal","data center power bills","rural hospital closures"]),
 ("HI","Hawaii","Pacific",2.9,98000,2000,25,["tourism","military","agriculture"],["highest cost of living in US","Maui wildfire recovery","outmigration of residents"]),
 ("ID","Idaho","Mountain West",3.5,75000,1250,30,["agriculture","tech","timber"],["housing costs from in-migration","property tax relief fight","Medicaid budget cuts"]),
 ("IL","Illinois","Great Lakes",4.8,81000,1250,30,["finance","manufacturing","agriculture","logistics"],["pension debt","transit fiscal cliff","property tax burden"]),
 ("IN","Indiana","Great Lakes",3.6,70000,1050,36,["manufacturing","pharma","logistics"],["property tax overhaul","Medicaid cost overrun","factory layoffs"]),
 ("IA","Iowa","Midwest",3.5,73000,950,33,["agriculture","insurance","wind energy"],["farm income decline","tariff retaliation on exports","CO2 pipeline eminent domain"]),
 ("KS","Kansas","Plains",3.8,72000,1000,28,["aviation manufacturing","agriculture","oil"],["Ogallala aquifer depletion","rural depopulation","Medicaid expansion stalemate"]),
 ("KY","Kentucky","South",5.0,62000,950,42,["auto manufacturing","bourbon","coal","logistics"],["coal job losses","flood recovery in eastern counties","SNAP cost-shift"]),
 ("LA","Louisiana","Gulf",4.3,58000,1050,46,["oil & gas","petrochemicals","shipping","seafood"],["insurance market collapse","coastal land loss","income tax overhaul","refinery layoffs"]),
 ("ME","Maine","Northeast",3.3,74000,1150,36,["tourism","fishing","forest products"],["housing shortage","aging workforce","federal funding freeze disputes"]),
 ("MD","Maryland","Mid-Atlantic",3.3,101000,1650,29,["federal government","biotech","cybersecurity"],["federal workforce layoffs","transit funding gap","budget deficit"]),
 ("MA","Massachusetts","Northeast",4.6,100000,1800,33,["biotech","higher education","finance","healthcare"],["research funding cuts","housing affordability","MBTA fiscal cliff","migrant shelter costs"]),
 ("MI","Michigan","Great Lakes",5.3,70000,1100,37,["auto manufacturing","agriculture","tourism"],["auto tariff whiplash","EV plant cancellations","road funding fight"]),
 ("MN","Minnesota","Midwest",3.3,87000,1300,28,["healthcare","medical devices","agriculture","retail HQ"],["property tax increases","state budget deficit forecast","rural hospital closures"]),
 ("MS","Mississippi","Deep South",3.8,54000,950,45,["agriculture","shipbuilding","manufacturing"],["income tax elimination gamble","Jackson water system","hospital closures"]),
 ("MO","Missouri","Midwest",3.9,69000,1050,37,["aerospace","agriculture","healthcare","logistics"],["abortion amendment repeal fight","St. Louis tornado recovery","stadium subsidy dispute"]),
 ("MT","Montana","Mountain West",3.0,70000,1100,40,["agriculture","tourism","mining"],["property tax surge on homeowners","housing costs from newcomers","public land access"]),
 ("NE","Nebraska","Plains",3.0,75000,1050,29,["agriculture","insurance","transportation"],["property tax relief fight","farm income decline","water allocation"]),
 ("NV","Nevada","Southwest",5.5,76000,1500,30,["gaming","tourism","mining","logistics"],["tourism slump layoffs","housing costs","water cuts from Colorado River"]),
 ("NH","New Hampshire","Northeast",3.0,97000,1500,28,["manufacturing","healthcare","tourism"],["housing shortage","property tax burden","electric rates"]),
 ("NJ","New Jersey","Mid-Atlantic",4.8,101000,1700,27,["pharma","finance","logistics"],["highest property taxes in US","transit fare hikes","electric bill spike"]),
 ("NM","New Mexico","Southwest",4.4,62000,1100,42,["oil & gas","federal labs","tourism"],["oil revenue dependence","water scarcity","child welfare system crisis"]),
 ("NY","New York","Mid-Atlantic",4.1,84000,1700,36,["finance","media","healthcare","tech"],["housing affordability","Medicaid cost shift to state","transit funding","congestion pricing dispute"]),
 ("NC","North Carolina","South",3.6,70000,1250,34,["banking","biotech","manufacturing","agriculture"],["Hurricane Helene recovery","Medicaid funding uncertainty","housing costs in metros"]),
 ("ND","North Dakota","Plains",2.6,76000,950,27,["oil","agriculture"],["oil price volatility","property tax elimination push","farm export tariffs"]),
 ("OH","Ohio","Great Lakes",4.8,69000,1050,36,["manufacturing","healthcare","logistics","finance"],["property tax revolt","Intel plant delays","SNAP and Medicaid cost shift"]),
 ("OK","Oklahoma","Plains",3.2,63000,1000,34,["oil & gas","aerospace","agriculture"],["income tax cut vs school funding","tornado and drought losses","tribal jurisdiction disputes"]),
 ("OR","Oregon","Pacific Northwest",4.7,80000,1500,33,["tech","timber","agriculture","outdoor gear"],["homelessness response costs","transportation funding gap","timber county revenue loss"]),
 ("PA","Pennsylvania","Mid-Atlantic",3.8,76000,1200,36,["healthcare","manufacturing","natural gas","education"],["transit funding crisis","electric rate increases","data center power demand"]),
 ("RI","Rhode Island","Northeast",4.8,85000,1400,34,["healthcare","education","defense"],["Washington Bridge closure","housing shortage","hospital system finances"]),
 ("SC","South Carolina","South",4.2,68000,1250,32,["auto manufacturing","aerospace","tourism","ports"],["coastal insurance costs","income tax cut fight","power rate increases"]),
 ("SD","South Dakota","Plains",1.9,73000,950,34,["agriculture","finance","tourism"],["property tax relief fight","CO2 pipeline eminent domain","farm export tariffs"]),
 ("TN","Tennessee","South",3.6,67000,1250,38,["healthcare","auto manufacturing","music","logistics"],["school voucher costs","housing costs in Nashville","rural hospital closures"]),
 ("TX","Texas","Southwest",4.1,76000,1400,32,["oil & gas","tech","manufacturing","ports"],["property tax burden","grid reliability","Hill Country flood recovery","home insurance costs"]),
 ("UT","Utah","Mountain West",3.3,95000,1450,25,["tech","finance","tourism","mining"],["housing costs for young families","Great Salt Lake decline","water allocation"]),
 ("VT","Vermont","Northeast",2.7,82000,1250,36,["tourism","education","healthcare","dairy"],["property tax spike from school costs","housing shortage","flood recovery"]),
 ("VA","Virginia","Mid-Atlantic",3.4,91000,1550,24,["federal government","defense","tech","agriculture"],["federal workforce layoffs","data center power bills","toll road costs"]),
 ("WA","Washington","Pacific Northwest",4.5,95000,1750,28,["tech","aerospace","agriculture","ports"],["state budget shortfall","new taxes fight","Boeing layoffs","ferry system failures"]),
 ("WV","West Virginia","South",4.0,56000,850,42,["coal","natural gas","healthcare"],["coal job losses","PEIA health premium hikes","foster care system strain"]),
 ("WI","Wisconsin","Great Lakes",3.2,75000,1050,30,["manufacturing","dairy","healthcare"],["farm closures","shared revenue fight","Microsoft data center power deal"]),
 ("WY","Wyoming","Mountain West",3.0,73000,950,33,["coal","oil & gas","tourism"],["coal revenue decline","property tax cap","federal land use disputes"]),
]

# Approximate recent presidential margins (points; positive = leans right). Political culture, not demographics.
LEAN = {'AL': 31, 'AK': 13, 'AZ': 5, 'AR': 31, 'CA': -20, 'CO': -11, 'CT': -15, 'DE': -15, 'FL': 13, 'GA': 2, 'HI': -24, 'ID': 37, 'IL': -11, 'IN': 19, 'IA': 13, 'KS': 16, 'KY': 31, 'LA': 22, 'ME': -7, 'MD': -29, 'MA': -25, 'MI': 1, 'MN': -4, 'MS': 23, 'MO': 19, 'MT': 20, 'NE': 21, 'NV': 3, 'NH': -3, 'NJ': -6, 'NM': -6, 'NY': -12, 'NC': 3, 'ND': 37, 'OH': 11, 'OK': 35, 'OR': -15, 'PA': 2, 'RI': -14, 'SC': 18, 'SD': 30, 'TN': 30, 'TX': 14, 'UT': 22, 'VT': -32, 'VA': -6, 'WA': -19, 'WV': 42, 'WI': 1, 'WY': 46}

ADJ = {
 "AL":["MS","TN","GA","FL"], "AK":[], "AZ":["CA","NV","UT","NM","CO"], "AR":["MO","TN","MS","LA","TX","OK"],
 "CA":["OR","NV","AZ"], "CO":["WY","NE","KS","OK","NM","AZ","UT"], "CT":["NY","MA","RI"], "DE":["MD","PA","NJ"],
 "FL":["AL","GA"], "GA":["FL","AL","TN","NC","SC"], "HI":[], "ID":["MT","WY","UT","NV","OR","WA"],
 "IL":["IN","KY","MO","IA","WI"], "IN":["MI","OH","KY","IL"], "IA":["MN","WI","IL","MO","NE","SD"],
 "KS":["NE","MO","OK","CO"], "KY":["IN","OH","WV","VA","TN","MO","IL"], "LA":["TX","AR","MS"],
 "ME":["NH"], "MD":["VA","WV","PA","DE"], "MA":["RI","CT","NY","NH","VT"], "MI":["OH","IN","WI"],
 "MN":["WI","IA","SD","ND"], "MS":["LA","AR","TN","AL"], "MO":["IA","IL","KY","TN","AR","OK","KS","NE"],
 "MT":["ND","SD","WY","ID"], "NE":["SD","IA","MO","KS","CO","WY"], "NV":["ID","UT","AZ","CA","OR"],
 "NH":["VT","ME","MA"], "NJ":["DE","PA","NY"], "NM":["AZ","UT","CO","OK","TX"], "NY":["NJ","PA","VT","MA","CT"],
 "NC":["VA","TN","GA","SC"], "ND":["MN","SD","MT"], "OH":["PA","WV","KY","IN","MI"], "OK":["KS","MO","AR","TX","NM","CO"],
 "OR":["CA","NV","ID","WA"], "PA":["NY","NJ","DE","MD","WV","OH"], "RI":["CT","MA"], "SC":["GA","NC"],
 "SD":["ND","MN","IA","NE","WY","MT"], "TN":["KY","VA","NC","GA","AL","MS","AR","MO"], "TX":["NM","OK","AR","LA"],
 "UT":["ID","WY","CO","NM","AZ","NV"], "VT":["NY","NH","MA"], "VA":["NC","TN","KY","WV","MD"],
 "WA":["ID","OR"], "WV":["OH","PA","MD","VA","KY"], "WI":["MI","MN","IA","IL"], "WY":["MT","SD","NE","CO","UT","ID"],
}

for a, ns in ADJ.items():
    for b in ns:
        assert a in ADJ[b], f"asymmetric adjacency {a}-{b}"

states = []
for code, name, region, unemp, inc, rent, fed, ind, pol in S:
    rent_burden = round(rent * 12 / inc * 100, 1)
    states.append({
        "code": code, "name": name, "region": region,
        "unemployment_pct": unemp, "median_income": inc, "median_rent": rent,
        "rent_burden_pct": rent_burden, "federal_funds_share_pct": fed,
        "key_industries": ind, "policy_pressures": pol, "neighbors": ADJ[code], "partisan_lean": LEAN[code],
    })

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "states.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump({"_note": "Approximate hand-entered values (BLS LAUS mid-2025, ACS 2023). Illustrative policy pressures. Refresh with scripts/fetch_data.py.",
               "states": states}, f, indent=1)
print(f"wrote {len(states)} states -> {out}")
