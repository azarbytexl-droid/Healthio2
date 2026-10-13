import os, json, base64, re, logging
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
API_KEY = os.getenv("ANTHROPIC_API_KEY")
client = Anthropic(api_key=API_KEY) if API_KEY else None
logger = logging.getLogger(__name__)

EXTRACT_PROMPT = """You are a medical document parser. Read this document (printed or handwritten).
Return ONLY valid JSON, no markdown, with this schema:
{"doc_type":"prescription|lab_report|scan|discharge_summary","date":"YYYY-MM-DD or null",
"doctor":str|null,"hospital":str|null,"diagnoses":[str],
"medicines":[{"name":str,"dosage":str,"frequency":str,"times":["morning","afternoon","night"],"with_food":"before|after|any","duration_days":int|null}],
"lab_results":[{"test":str,"value":number|null,"unit":str,"reference_range":str,"flag":"low|normal|high|critical"}],
"findings":str|null,"follow_up":str|null}
Use null when unreadable. Never invent values."""


def _fallback_extract():
    return {
        "doc_type": "unprocessed_document",
        "date": None,
        "doctor": None,
        "hospital": None,
        "diagnoses": [],
        "medicines": [],
        "lab_results": [],
        "findings": "Automated extraction was unavailable. No medical information was interpreted; review the original document with a clinician.",
        "follow_up": "Consult your doctor or pharmacist before making care or medication decisions.",
    }


def _fallback_explain(extracted, lang="English"):
    conditions = extracted.get("diagnoses") or []
    abnormal = []
    for lab in extracted.get("lab_results", []):
        if lab.get("flag") in {"low", "high", "critical"}:
            abnormal.append(f"{lab.get('test', 'Test')}: {lab.get('value')} {lab.get('unit', '')} ({lab.get('flag')})")
    if abnormal:
        issue = "Important values to review: " + "; ".join(abnormal)
    else:
        issue = "No lab values were available to evaluate; this does not mean results are normal."
    return (
        f"This record appears to be a {extracted.get('doc_type', 'medical record')} in {lang}.\n\n"
        f"Key medical items: {', '.join(conditions) if conditions else 'No diagnoses or medical values were extracted.'}\n\n"
        f"{issue}\n\n"
        "Please ask your doctor to explain the details and confirm the plan.\n"
        "Consult your doctor before changing medicines or lifestyle."
    )


def _fallback_diet_plan(conditions, lang="English", region="South Indian"):
    items = conditions or ["general wellness"]
    label = ', '.join(items)
    return (
        f"## {region} meal plan ({lang})\n\n"
        f"For: {label}\n\n"
        "| Time | Meal | Notes |\n"
        "| --- | --- | --- |\n"
        "| Breakfast | Idli, sambar, vegetables | Easy to digest, not too salty |\n"
        "| Mid-morning | Buttermilk or fruit | Hydration and light nutrition |\n"
        "| Lunch | Brown rice, dal, curd, leafy vegetables | Favour simple, balanced portions |\n"
        "| Snack | Fruit or nuts in small quantity | Avoid heavy fried snacks |\n"
        "| Dinner | Khichdi, vegetables, soup | Light and easy on digestion |\n\n"
        "Foods to avoid: highly processed foods, excess sugar, salty snacks, and fried foods.\n\n"
        "Hydration: sip water steadily through the day.\n\n"
        "General guidance, not medical advice."
    )


def _json(text):
    if isinstance(text, dict):
        return text
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("No JSON object found")
    return json.loads(match.group(0))


def extract(file_bytes, media_type):
    fallback = _fallback_extract()
    if client is None:
        return fallback
    b64 = base64.standard_b64encode(file_bytes).decode()
    kind = "document" if media_type == "application/pdf" else "image"
    try:
        r = client.messages.create(model=MODEL, max_tokens=2500, messages=[{"role": "user", "content": [
            {"type": kind, "source": {"type": "base64", "media_type": media_type, "data": b64}},
            {"type": "text", "text": EXTRACT_PROMPT}]}])
        text = getattr(r.content[0], "text", str(r.content[0]))
        return _json(text)
    except Exception:
        logger.exception("Medical document extraction failed")
        return fallback


def explain(extracted, lang="English"):
    fallback = _fallback_explain(extracted, lang)
    if client is None:
        return fallback
    try:
        body = (
            f"Explain this medical record to a non-medical patient in simple {lang} (8th-grade level).\n"
            "Sections: 1) What this is 2) What is out of range and what it may mean (mark with ⚠️) 3) What to ask your doctor.\n"
            "Do not diagnose. End with one line telling them to consult their doctor.\n"
            f"DATA: {json.dumps(extracted)}"
        )
        r = client.messages.create(model=MODEL, max_tokens=1200, messages=[{"role": "user", "content": body}])
        return r.content[0].text
    except Exception:
        logger.exception("Medical record explanation failed")
        return fallback


def diet_plan(conditions, lang="English", region="South Indian"):
    fallback = _fallback_diet_plan(conditions, lang, region)
    if client is None:
        return fallback
    try:
        body = (
            f"Create a 1-day {region} meal plan in {lang} for a person with: {', '.join(conditions) or 'general wellness'}.\n"
            "Give breakfast, mid-morning, lunch, snack, dinner with times, foods to avoid, and a hydration note.\n"
            "Format as a short markdown table. Add: 'General guidance, not medical advice.'"
        )
        r = client.messages.create(model=MODEL, max_tokens=1500, messages=[{"role": "user", "content": body}])
        return r.content[0].text
    except Exception:
        logger.exception("Diet plan generation failed")
        return fallback

SYSTEM = """You are a careful health-education assistant, not a clinician. Reply in the user's language using plain, calm wording.
Use only the patient record context provided when referring to their personal history, test results, or medicines. Uploaded-document medicine details are automated extractions and may be wrong or incomplete; identify the document date when available and tell the user to check the original prescription with a clinician or pharmacist.
Answer general questions about a named medicine even when it is not in the patient's records. When you are confident, explain its common approved uses, common side effects, and important label warnings in general terms. Make clear that uses and risks can vary by country, brand, formulation, and person. If the medicine name is ambiguous, unfamiliar, or the evidence is uncertain, say so instead of guessing and ask for the exact active ingredient or suggest checking an official medicine label with a pharmacist.
Never recommend starting, stopping, substituting, or changing a medicine or dose; never provide a personalized dose or claim that a medicine is safe or interaction-free. For interaction questions, ask for the exact names and strengths of all medicines, supplements, and relevant over-the-counter products if needed, explain that a pharmacist or prescriber must check the complete list, and do not declare the combination safe. Give extra caution for children, pregnancy, older adults, allergies, and chronic conditions; direct personalized decisions to a clinician or pharmacist. Do not invent medicine names, uses, side effects, warnings, diagnoses, lab results, or treatment plans.
Offer general triage guidance only. For severe chest pain, difficulty breathing, stroke signs, heavy bleeding, unconsciousness, or another immediate danger, tell the user to contact local emergency services now and not continue chatting.
Do not create reminders or alter schedules; direct users to the reminder form. Do not claim to have taken any action or contacted care services."""


def chat(history, message, records_ctx, lang):
    if client is None:
        raise RuntimeError("AI chat is not configured. Set ANTHROPIC_API_KEY to enable this assistant.")

    msgs = history[-10:] + [{"role": "user", "content": message}]
    try:
        r = client.messages.create(model=MODEL, max_tokens=900,
            system=f"{SYSTEM}\nLanguage: {lang}\nPATIENT RECORDS:\n{json.dumps(records_ctx)[:6000]}", messages=msgs)
        return r.content[0].text
    except Exception:
        logger.exception("Health chat response generation failed")
        raise RuntimeError("AI chat is temporarily unavailable. Please try again later.") from None
