from intent_engine import build_searchable_text


def test_metadata_fields_included_in_searchable_text():
    obj = {
        "title": "Titulo",
        "text": "Texto",
        "metadata": {
            "category": "Cat1",
            "tema": "Tema2",
            "tipo_fragmento": "Frag3",
            "seccion": "Sec4",
            "priority": 7,
            "faq_id": "Faq5",
        },
    }

    result = build_searchable_text(obj)

    for token in ["cat1", "tema2", "frag3", "sec4", "7", "faq5"]:
        assert token in result
