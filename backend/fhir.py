"""Map extracted JSON -> FHIR R4 resources (ABDM profiles are FHIR R4 based)."""
import uuid

def _ref(pid): return {"reference": f"Patient/{pid}"}

def to_bundle(patient_id, ex):
    entries = []
    for lab in ex.get("lab_results", []):
        obs = {"resourceType": "Observation", "id": str(uuid.uuid4()), "status": "final",
               "code": {"text": lab["test"]}, "subject": _ref(patient_id),
               "effectiveDateTime": ex.get("date"),
               "valueQuantity": {"value": lab.get("value"), "unit": lab.get("unit")},
               "referenceRange": [{"text": lab.get("reference_range")}],
               "interpretation": [{"text": lab.get("flag", "normal")}]}
        entries.append({"resource": obs})
    for m in ex.get("medicines", []):
        entries.append({"resource": {"resourceType": "MedicationRequest", "id": str(uuid.uuid4()),
            "status": "active", "intent": "order", "subject": _ref(patient_id),
            "medicationCodeableConcept": {"text": m["name"]}, "authoredOn": ex.get("date"),
            "dosageInstruction": [{"text": f'{m.get("dosage","")} {m.get("frequency","")} x {m.get("duration_days","?")}d'}]}})
    for d in ex.get("diagnoses", []):
        entries.append({"resource": {"resourceType": "Condition", "id": str(uuid.uuid4()),
            "code": {"text": d}, "subject": _ref(patient_id), "recordedDate": ex.get("date")}})
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}
