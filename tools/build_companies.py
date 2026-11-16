"""Probe candidate company slugs on each ATS and write the ones that answer to config/companies.yaml.

Run: .venv\\Scripts\\python tools\\build_companies.py   (safe to re-run; it merges with what is there)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from app.config import CONFIG_DIR  # noqa: E402
from app.sources.ats import probe  # noqa: E402
from app.sources.common import client  # noqa: E402

CANDIDATES: dict[str, dict[str, str]] = {
    "greenhouse": {
        "razorpaysoftwareprivatelimited": "Razorpay", "groww": "Groww", "postman": "Postman", "browserstack": "BrowserStack",
        "hackerrank": "HackerRank", "druva": "Druva", "cohesity": "Cohesity", "rubrik": "Rubrik", "stripe": "Stripe",
        "airbnb": "Airbnb", "databricks": "Databricks", "cloudflare": "Cloudflare", "mongodb": "MongoDB",
        "elastic": "Elastic", "gitlab": "GitLab", "okta": "Okta", "twilio": "Twilio", "coinbase": "Coinbase",
        "robinhood": "Robinhood", "datadog": "Datadog", "figma": "Figma", "discord": "Discord", "dropbox": "Dropbox",
        "pinterest": "Pinterest", "reddit": "Reddit", "instacart": "Instacart", "lyft": "Lyft", "doordashusa": "DoorDash",
        "anthropic": "Anthropic", "scaleai": "Scale AI", "airtable": "Airtable", "asana": "Asana", "brex": "Brex",
        "gusto": "Gusto", "samsara": "Samsara", "sumologic": "Sumo Logic", "nutanix": "Nutanix", "zscaler": "Zscaler",
        "inmobi": "InMobi", "mindtickle": "Mindtickle", "chargebee": "Chargebee", "clevertap": "CleverTap",
        "innovaccer": "Innovaccer", "zeta": "Zeta", "sliceit": "slice", "uptycs": "Uptycs", "highradius": "HighRadius",
        "sprinklr": "Sprinklr", "moengage": "MoEngage", "whatfix": "Whatfix", "hasura": "Hasura", "appsmith": "Appsmith",
        "toddle": "Toddle", "cleartax": "ClearTax", "upgrad": "upGrad", "zepto": "Zepto", "urbancompany": "Urban Company",
        "nykaa": "Nykaa", "rapido": "Rapido", "porter": "Porter", "spinny": "Spinny", "cars24": "CARS24",
        "makemytrip": "MakeMyTrip", "practo": "Practo", "thoughtspot": "ThoughtSpot", "gupshup": "Gupshup",
        "yellowai": "Yellow.ai", "sigmoid": "Sigmoid", "tekion": "Tekion", "icertis": "Icertis", "amagi": "Amagi",
        "lenskart": "Lenskart", "glance": "Glance", "games24x7": "Games24x7", "mpl": "MPL", "winzo": "WinZO",
        "agoda": "Agoda", "grab": "Grab", "wise": "Wise", "deliveryhero": "Delivery Hero", "n26": "N26",
        "phonepe": "PhonePe", "sharechat": "ShareChat", "dream11": "Dream11", "swiggy": "Swiggy", "atlan": "Atlan",
        "observeai": "Observe.AI", "fivetran": "Fivetran", "celonis": "Celonis", "harness": "Harness",
        "confluent": "Confluent", "hashicorp": "HashiCorp", "newrelic": "New Relic", "pagerduty": "PagerDuty",
        "affirm": "Affirm", "chime": "Chime", "toast": "Toast", "squarespace": "Squarespace", "duolingo": "Duolingo",
        "lattice": "Lattice", "flexport": "Flexport", "marqeta": "Marqeta", "carta": "Carta", "verkada": "Verkada",
        "intercom": "Intercom", "contentful": "Contentful", "miro": "Miro", "canva": "Canva", "turing": "Turing",
        "cognite": "Cognite", "nium": "Nium", "airwallex": "Airwallex", "dataiku": "Dataiku", "algolia": "Algolia",
        "mozilla": "Mozilla", "grammarly": "Grammarly", "zendesk": "Zendesk", "appdynamics": "AppDynamics",
        "cohere": "Cohere", "imc": "IMC Trading", "tower": "Tower Research", "graviton": "Graviton",
        "quadeye": "Quadeye", "jumptrading": "Jump Trading", "hudsonrivertrading": "Hudson River Trading",
    },
    "lever": {
        "cred": "CRED", "meesho": "Meesho", "netflix": "Netflix", "palantir": "Palantir", "kong": "Kong",
        "spotify": "Spotify", "plaid": "Plaid", "attentive": "Attentive", "wealthsimple": "Wealthsimple",
        "jupiter-money": "Jupiter", "khatabook": "Khatabook", "bharatpe": "BharatPe", "zeptonow": "Zepto",
        "atherenergy": "Ather Energy", "pocketfm": "Pocket FM", "smallcase": "smallcase", "inshorts": "Inshorts",
        "juspay": "Juspay", "upstox": "Upstox", "paytm": "Paytm", "unacademy": "Unacademy", "dunzo": "Dunzo",
        "curefit": "cult.fit", "fampay": "FamPay", "sharechat": "ShareChat", "mindtickle": "Mindtickle",
        "eightfold": "Eightfold", "rippling": "Rippling", "outreach": "Outreach", "greenhouse": "Greenhouse",
        "shieldai": "Shield AI", "binance": "Binance", "kraken": "Kraken", "mistral": "Mistral AI",
        "anyscale": "Anyscale", "weave": "Weave", "nielseniq": "NielsenIQ", "tide": "Tide", "freshtohome": "FreshToHome",
        "zomato": "Zomato", "ola": "Ola", "oyo": "OYO", "licious": "Licious", "acko": "ACKO", "digit": "Go Digit",
        "uber": "Uber", "jar": "Jar", "scapia": "Scapia", "rupeek": "Rupeek", "vymo": "Vymo", "leadsquared": "LeadSquared",
        "nobroker": "NoBroker", "simpl": "Simpl", "hevodata": "Hevo Data", "spotdraft": "SpotDraft",
        "atlassian": "Atlassian", "dailyhunt": "Dailyhunt", "pharmeasy": "PharmEasy", "groupon": "Groupon",
    },
    "ashby": {
        "notion": "Notion", "openai": "OpenAI", "ramp": "Ramp", "linear": "Linear", "vercel": "Vercel",
        "supabase": "Supabase", "posthog": "PostHog", "replit": "Replit", "anysphere": "Cursor (Anysphere)",
        "perplexity": "Perplexity", "elevenlabs": "ElevenLabs", "cohere": "Cohere", "deel": "Deel", "retool": "Retool",
        "sentry": "Sentry", "clickhouse": "ClickHouse", "langchain": "LangChain", "pinecone": "Pinecone",
        "modal": "Modal", "airbyte": "Airbyte", "zapier": "Zapier", "mercor": "Mercor", "sarvam": "Sarvam AI",
        "composio": "Composio", "firstwork": "Firstwork", "certa": "Certa", "dub": "Dub", "cal": "Cal.com",
        "resend": "Resend", "plane": "Plane", "rocketlane": "Rocketlane", "zania": "Zania", "lovable": "Lovable",
        "browserbase": "Browserbase", "e2b": "E2B", "neon": "Neon", "railway": "Railway", "temporal": "Temporal",
        "snyk": "Snyk", "docker": "Docker", "multiplier": "Multiplier", "sardine": "Sardine", "harvey": "Harvey",
        "decagon": "Decagon", "sierra": "Sierra", "glean": "Glean", "writer": "Writer", "runway": "Runway",
        "pika": "Pika", "together": "Together AI", "fireworks-ai": "Fireworks AI", "baseten": "Baseten",
        "nomic": "Nomic", "weaviate": "Weaviate", "qdrant": "Qdrant", "hightouch": "Hightouch", "hex": "Hex",
        "mintlify": "Mintlify", "warp": "Warp", "raycast": "Raycast", "clerk": "Clerk", "workos": "WorkOS",
        "inngest": "Inngest", "trigger.dev": "Trigger.dev", "zed": "Zed", "astronomer": "Astronomer",
        "kapa": "Kapa.ai", "atlan": "Atlan", "skit": "Skit.ai", "ema": "Ema", "uniphore": "Uniphore",
        "pepper": "Pepper", "pibit": "Pibit.ai", "truefoundry": "TrueFoundry", "portkey": "Portkey",
    },
    "smartrecruiters": {
        "BoschGroup": "Bosch", "Freshworks": "Freshworks", "Visa": "Visa", "ServiceNow": "ServiceNow",
        "Experian": "Experian", "WesternDigital": "Western Digital", "PublicisSapient": "Publicis Sapient",
        "AbbVie": "AbbVie", "Equinix": "Equinix", "IKEA": "IKEA", "Ubisoft2": "Ubisoft", "Wipro": "Wipro",
        "LTIMindtree": "LTIMindtree", "Infosys": "Infosys", "SquarePoint": "Squarepoint", "Allegis": "Allegis",
        "Collabera": "Collabera", "NTTDATA": "NTT DATA", "DeliveryHero": "Delivery Hero", "Sodexo": "Sodexo",
        "Ciklum": "Ciklum", "Endava": "Endava", "EPAM": "EPAM", "Globant": "Globant", "Nagarro": "Nagarro",
        "Capgemini": "Capgemini", "Sopra": "Sopra Steria", "Atos": "Atos", "Mphasis": "Mphasis", "Zensar": "Zensar",
        "Mondelez": "Mondelez", "LinkedIn": "LinkedIn", "Canva": "Canva", "Avaloq": "Avaloq",
    },
    "workable": {
        "huggingface": "Hugging Face", "kpmg-india": "KPMG India", "toptal": "Toptal", "remote": "Remote.com",
        "smartly": "Smartly", "turing": "Turing", "proxify": "Proxify", "hotjar": "Hotjar", "personio": "Personio",
        "miro": "Miro", "doist": "Doist", "toggl": "Toggl", "taxfix": "Taxfix", "zego": "Zego", "bolt": "Bolt",
        "pipedrive": "Pipedrive", "omnipresent": "Omnipresent", "invideo": "InVideo", "bluestacks": "BlueStacks",
        "flobiz": "FloBiz", "wingify": "Wingify", "kissflow": "Kissflow", "zoho": "Zoho", "hackerearth": "HackerEarth",
        "instamojo": "Instamojo", "shiprocket": "Shiprocket", "xoxoday": "Xoxoday", "exotel": "Exotel",
    },
    "workday": {
        "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite": "NVIDIA",
        "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site": "Salesforce",
        "https://walmart.wd5.myworkdayjobs.com/WalmartExternal": "Walmart",
        "https://adobe.wd5.myworkdayjobs.com/external_experienced": "Adobe",
        "https://intel.wd1.myworkdayjobs.com/External": "Intel",
        "https://mastercard.wd1.myworkdayjobs.com/CorporateCareers": "Mastercard",
        "https://dell.wd1.myworkdayjobs.com/External": "Dell",
        "https://hp.wd5.myworkdayjobs.com/ExternalCareerSite": "HP",
        "https://paypal.wd1.myworkdayjobs.com/jobs": "PayPal",
        "https://target.wd5.myworkdayjobs.com/targetcareers": "Target",
        "https://pwc.wd3.myworkdayjobs.com/Global_Experienced_Careers": "PwC",
        "https://barclays.wd3.myworkdayjobs.com/External_Career_Site_Barclays": "Barclays",
        "https://citi.wd5.myworkdayjobs.com/2": "Citi",
        "https://workday.wd5.myworkdayjobs.com/Workday": "Workday",
        "https://autodesk.wd1.myworkdayjobs.com/Ext": "Autodesk",
        "https://synopsys.wd1.myworkdayjobs.com/careers": "Synopsys",
        "https://micron.wd1.myworkdayjobs.com/External": "Micron",
        "https://broadcom.wd1.myworkdayjobs.com/External_Career": "Broadcom",
        "https://motorolasolutions.wd5.myworkdayjobs.com/Careers": "Motorola Solutions",
        "https://thomsonreuters.wd5.myworkdayjobs.com/External_Career_Site": "Thomson Reuters",
        "https://philips.wd3.myworkdayjobs.com/jobs-and-careers": "Philips",
        "https://redhat.wd5.myworkdayjobs.com/jobs": "Red Hat",
        "https://crowdstrike.wd5.myworkdayjobs.com/crowdstrikecareers": "CrowdStrike",
        "https://netapp.wd1.myworkdayjobs.com/NetAppCareers": "NetApp",
        "https://kla.wd1.myworkdayjobs.com/Search": "KLA",
        "https://analogdevices.wd1.myworkdayjobs.com/External": "Analog Devices",
        "https://cadence.wd1.myworkdayjobs.com/External_Careers": "Cadence",
        "https://warnerbros.wd5.myworkdayjobs.com/global": "Warner Bros. Discovery",
        "https://statestreet.wd1.myworkdayjobs.com/Global": "State Street",
        "https://boeing.wd1.myworkdayjobs.com/EXTERNAL_CAREERS": "Boeing",
        "https://cisco.wd5.myworkdayjobs.com/Cisco_Careers": "Cisco",
        "https://amex.wd5.myworkdayjobs.com/AmexCareers": "American Express",
        "https://hpe.wd5.myworkdayjobs.com/Jobsathpe": "HPE",
        "https://gartner.wd5.myworkdayjobs.com/EXT": "Gartner",
        "https://zs.wd1.myworkdayjobs.com/ZS_Careers": "ZS Associates",
        "https://fmr.wd1.myworkdayjobs.com/FidelityCareers": "Fidelity",
        "https://wellsfargo.wd1.myworkdayjobs.com/WellsFargoJobs": "Wells Fargo",
        "https://blackrock.wd1.myworkdayjobs.com/BlackRock_Professional": "BlackRock",
        "https://bnymellon.wd1.myworkdayjobs.com/BNY_Careers": "BNY",
        "https://lowes.wd5.myworkdayjobs.com/LWS_External_CS": "Lowe's",
        "https://chevron.wd5.myworkdayjobs.com/jobs": "Chevron",
        "https://shell.wd3.myworkdayjobs.com/ShellCareers": "Shell",
        "https://honeywell.wd5.myworkdayjobs.com/Honeywell": "Honeywell",
        "https://3m.wd1.myworkdayjobs.com/Search": "3M",
        "https://caterpillar.wd5.myworkdayjobs.com/CaterpillarCareers": "Caterpillar",
        "https://deere.wd5.myworkdayjobs.com/External": "John Deere",
        "https://nokia.wd3.myworkdayjobs.com/en-US/nokia": "Nokia",
        "https://ericsson.wd3.myworkdayjobs.com/External": "Ericsson",
        "https://unilever.wd3.myworkdayjobs.com/Unilever_Experienced_Professionals": "Unilever",
        "https://siemens.wd3.myworkdayjobs.com/External": "Siemens",
        "https://kpmg.wd3.myworkdayjobs.com/KPMG": "KPMG",
        "https://ey.wd3.myworkdayjobs.com/EY_Careers": "EY",
        "https://accenture.wd3.myworkdayjobs.com/AccentureCareers": "Accenture",
        "https://deloitte.wd1.myworkdayjobs.com/DeloitteUSI": "Deloitte",
        "https://hsbc.wd3.myworkdayjobs.com/external": "HSBC",
        "https://db.wd3.myworkdayjobs.com/DBWebsite": "Deutsche Bank",
        "https://ms.wd5.myworkdayjobs.com/External": "Morgan Stanley",
        "https://nasdaq.wd1.myworkdayjobs.com/Global_External_Site": "Nasdaq",
        "https://comcast.wd5.myworkdayjobs.com/Comcast_Careers": "Comcast",
        "https://snowflake.wd1.myworkdayjobs.com/Snowflake_Careers": "Snowflake",
        "https://servicenow.wd1.myworkdayjobs.com/ServiceNow": "ServiceNow",
        "https://qualcomm.wd5.myworkdayjobs.com/External": "Qualcomm",
        "https://amd.wd1.myworkdayjobs.com/External": "AMD",
        "https://arm.wd3.myworkdayjobs.com/Careers": "Arm",
        "https://splunk.wd5.myworkdayjobs.com/External": "Splunk",
        "https://vmware.wd1.myworkdayjobs.com/VMware": "VMware",
        "https://zebra.wd1.myworkdayjobs.com/Zebra_Careers": "Zebra",
        "https://ptc.wd1.myworkdayjobs.com/PTC": "PTC",
        "https://sap.wd3.myworkdayjobs.com/SAPCareers": "SAP",
        "https://elsevier.wd5.myworkdayjobs.com/ElsevierJobs": "Elsevier",
        "https://relx.wd3.myworkdayjobs.com/relx": "RELX",
        "https://lseg.wd3.myworkdayjobs.com/Careers": "LSEG",
        "https://expedia.wd5.myworkdayjobs.com/search": "Expedia",
        "https://ebay.wd5.myworkdayjobs.com/apply": "eBay",
    },
}


async def main() -> None:
    path = CONFIG_DIR / "companies.yaml"
    existing = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    existing = existing or {}
    async with client() as c:
        for ats, cands in CANDIDATES.items():
            have = {e["key"] for e in existing.get(ats, []) or []}
            todo = [(k, n) for k, n in cands.items() if k not in have]
            sem = asyncio.Semaphore(1 if ats == "workday" else 8)

            async def check(k, n):
                async with sem:
                    return k, n, await probe(c, ats, k)

            res = await asyncio.gather(*(check(k, n) for k, n in todo))
            ok = [{"key": k, "name": n} for k, n, good in res if good]
            bad = [k for k, n, good in res if not good]
            existing[ats] = (existing.get(ats) or []) + ok
            print(f"{ats:16s} +{len(ok):3d} valid  ({len(bad)} rejected)")
    header = ("# Companies read directly from their own careers system (ATS). Built by tools/build_companies.py.\n"
              "# Add your own: greenhouse/lever/ashby/workable/smartrecruiters take the board slug from the careers URL,\n"
              "# workday takes the full careers-site URL (https://<tenant>.wdN.myworkdayjobs.com/<site>).\n")
    path.write_text(header + yaml.safe_dump(existing, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print("total", sum(len(v) for v in existing.values()), "companies ->", path)


if __name__ == "__main__":
    asyncio.run(main())
