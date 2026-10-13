from db import put, all_, get
DOCS = [
 ("Dr. Meena R","Cardiology","Erode",11.3410,77.7172,"+91-90000-00001","Heart Care Clinic, Perundurai Rd",True),
 ("Dr. Karthik S","Neurology","Salem",11.6643,78.1460,"+91-90000-00002","NeuroLife, Fairlands",False),
 ("Dr. Anitha P","Pediatrics","Namakkal",11.2189,78.1677,"+91-90000-00003","Little Steps Clinic",True),
 ("Dr. Suresh K","General Medicine","Kumarapalayam",11.4425,77.7270,"+91-90000-00004","Sri Ram Clinic, Bus Stand",True),
 ("Dr. Farida B","General Medicine","Erode",11.3500,77.7300,"+91-90000-00005","City Health Centre",True),
 ("Dr. Rajan T","Orthopedics","Salem",11.6700,78.1500,"+91-90000-00006","BoneCare, Five Roads",False),
]

USER_PROVIDED_DOCS = [
    {
        "id": "user-listing-dr-revathi-mohan",
        "name": "Dr. Revathi Mohan",
        "specialty": "Psychology",
        "city": "",
        "address": "",
        "focus": "Holistic well-being and emotional counseling",
    },
    {
        "id": "user-listing-dr-n-rajasekar",
        "name": "Dr. N. Rajasekar",
        "specialty": "Cardiology",
        "city": "Erode",
        "address": "Sudha Hospitals, Perundurai Rd",
        "focus": "Interventional Cardiologist",
        "qualifications": "MD, DNB (Cardio), MRCP (UK), FSCAI, FACC",
    },
    {
        "id": "user-listing-thangam-neurology",
        "name": "Thangam Hospital — Neurology Department",
        "specialty": "Neurology",
        "city": "",
        "address": "Thangam Hospital",
        "focus": "Neurology Department",
    },
]


def run():
    if not all_("doctors"):
        for i,(n,s,c,la,lo,p,a,home) in enumerate(DOCS):
            put("doctors", {"resourceType":"Practitioner","name":[{"text":n}],"specialty":s,"city":c,
                "telecom":[{"system":"phone","value":p}],"address":a,"location":{"lat":la,"lng":lo},
                "home_visit":home,"hpr_id":f"HPR-MOCK-{i+1:03d}"})
    for listing in USER_PROVIDED_DOCS:
        if not get("doctors", listing["id"]):
            put("doctors", {
                "id": listing["id"],
                "resourceType": "Practitioner",
                "name": [{"text": listing["name"]}],
                "specialty": listing["specialty"],
                "city": listing["city"],
                "address": listing["address"],
                "focus": listing["focus"],
                "qualifications": listing.get("qualifications"),
                "listing_source": "user-provided",
                "verification_status": "unverified",
            })
