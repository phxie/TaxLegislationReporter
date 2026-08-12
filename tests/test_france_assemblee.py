import datetime as dt
import io
import json
import zipfile

import pytest

from app.ingestion.france_assemblee import FranceAssembleeAdapter, _extract_status_events


def _dossier(
    uid="DLR5L17N1",
    titre="Proposition de loi visant a baisser la fiscalite de l'electricite",
    xsi_type="DossierLegislatif_Type",
    legislature="17",
    titre_chemin=None,
    procedure="Proposition de loi ordinaire",
):
    return {
        "dossierParlementaire": {
            "@xsi:type": xsi_type,
            "uid": uid,
            "legislature": legislature,
            "titreDossier": {"titre": titre, "titreChemin": titre_chemin or uid},
            "procedureParlementaire": {"code": "2", "libelle": procedure},
            "actesLegislatifs": {
                "acteLegislatif": {
                    "@xsi:type": "Etape_Type",
                    "libelleActe": {"libelleCourt": "1ère lecture"},
                    "dateActe": None,
                    "actesLegislatifs": {
                        "acteLegislatif": [
                            {
                                "@xsi:type": "DepotInitiative_Type",
                                "codeActe": "SN1-DEPOT",
                                "libelleActe": {"libelleCourt": "1er dépôt d'une initiative."},
                                "dateActe": "2024-06-27T00:00:00.000+02:00",
                                "actesLegislatifs": None,
                            },
                            {
                                "@xsi:type": "Etape_Type",
                                "codeActe": "SN1-COM",
                                "libelleActe": {"libelleCourt": "Renvoi en commission au fond"},
                                "dateActe": "2024-07-02T00:00:00.000+02:00",
                                "actesLegislatifs": None,
                            },
                        ]
                    },
                }
            },
        }
    }


def _build_zip(dossiers: list[dict]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for i, dossier in enumerate(dossiers):
            zf.writestr(f"json/dossierParlementaire/entry{i}.json", json.dumps(dossier))
    return buffer.getvalue()


def test_extract_status_events_walks_nested_tree():
    dossier = _dossier()["dossierParlementaire"]
    events = _extract_status_events(dossier["actesLegislatifs"])

    assert len(events) == 2
    assert events[0].event_date == dt.date(2024, 6, 27)
    assert events[0].action_text == "1er dépôt d'une initiative."
    assert events[1].event_date == dt.date(2024, 7, 2)


def test_fetch_updates_filters_tax_relevant_and_normalizes():
    dossiers = [
        _dossier(uid="DLR5L17N1", titre="Proposition de loi visant a baisser la fiscalite de l'electricite"),
        _dossier(
            uid="DLR5L17N2",
            titre="Proposition de loi visant a favoriser la participation a la vie democratique",
            procedure="Proposition de loi ordinaire",
        ),
    ]
    adapter = FranceAssembleeAdapter()
    adapter._download_zip = lambda: _build_zip(dossiers)

    results = list(adapter.fetch_updates(since=None))

    assert len(results) == 1
    bill = results[0]
    assert bill.jurisdiction == "FRANCE"
    assert bill.source_bill_id == "DLR5L17N1"
    assert bill.session == "17"
    assert bill.bill_number == "DLR5L17N1"
    assert bill.source_label == "Assemblée Nationale (data.assemblee-nationale.fr)"
    assert "fiscal" in bill.tax_keywords_matched
    assert bill.introduced_date == dt.date(2024, 6, 27)
    assert bill.last_action_date == dt.date(2024, 7, 2)
    assert bill.status_text == "Renvoi en commission au fond"
    assert bill.source_url == "https://www.assemblee-nationale.fr/dyn/17/dossiers/DLR5L17N1"
    assert bill.summary == "Proposition de loi ordinaire"


def test_fetch_updates_skips_non_bill_dossier_types():
    dossiers = [
        _dossier(uid="DLR5L17N3", titre="Allocution du President d'age", xsi_type="DossierResolutionAN")
    ]
    adapter = FranceAssembleeAdapter()
    adapter._download_zip = lambda: _build_zip(dossiers)

    results = list(adapter.fetch_updates(since=None))

    assert results == []


def test_fetch_updates_skips_other_legislatures():
    dossiers = [
        _dossier(uid="DLR5L16N1", titre="Proposition de loi fiscale ancienne", legislature="16"),
    ]
    adapter = FranceAssembleeAdapter(legislature="17")
    adapter._download_zip = lambda: _build_zip(dossiers)

    results = list(adapter.fetch_updates(since=None))

    assert results == []


def test_fetch_updates_raises_when_zip_has_no_dossier_entries():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("json/document/somefile.json", "{}")

    adapter = FranceAssembleeAdapter()
    adapter._download_zip = lambda: buffer.getvalue()

    with pytest.raises(RuntimeError):
        list(adapter.fetch_updates(since=None))
