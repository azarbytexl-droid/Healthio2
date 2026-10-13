import types

import ai


def test_extract_handles_api_failure(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("missing API key")

    monkeypatch.setattr(
        ai,
        "client",
        types.SimpleNamespace(messages=types.SimpleNamespace(create=boom)),
    )

    result = ai.extract(b"fake-bytes", "image/jpeg")

    assert result["doc_type"] in {"prescription", "lab_report", "scan", "discharge_summary"}
    assert isinstance(result["medicines"], list)
    assert isinstance(result["lab_results"], list)
    assert "diagnoses" in result


def test_explain_handles_api_failure(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("missing API key")

    monkeypatch.setattr(
        ai,
        "client",
        types.SimpleNamespace(messages=types.SimpleNamespace(create=boom)),
    )

    result = ai.explain({"diagnoses": ["Hypertension"], "lab_results": []}, "Tamil")

    assert "Tamil" in result or "Hypertension" in result or "doctor" in result.lower()


def test_chat_answers_general_medicine_questions_with_safety_limits(monkeypatch):
    captured = {}

    def respond(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(text="Paracetamol is commonly used for pain and fever.")],
        )

    monkeypatch.setattr(
        ai,
        "client",
        types.SimpleNamespace(messages=types.SimpleNamespace(create=respond)),
    )

    result = ai.chat([], "What is paracetamol used for?", [], "English")

    assert "commonly used for pain and fever" in result
    assert "Answer general questions about a named medicine" in captured["system"]
    assert "Never recommend starting, stopping, substituting, or changing a medicine or dose" in captured["system"]
    assert "Language: English" in captured["system"]
