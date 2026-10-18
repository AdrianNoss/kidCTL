"""Eigene Programme der Eltern (z. B. git): Dialog, Validierung, Policy, Entfernen."""

import pytest

from app import programs
from test_policy import _policy, _setup, device


def test_custom_key_validation():
    assert programs.custom_key("git") == "exe:/usr/bin/git"
    assert programs.custom_key(" /usr/bin/git ") == "exe:/usr/bin/git"
    assert programs.custom_key("/opt/foo-1.2/bin/foo_x") == "exe:/opt/foo-1.2/bin/foo_x"
    for bad in ("", "/", "usr/bin/git", "/usr/bin/../bin/sh", "/usr//bin/git", "/usr/bin/", "/usr/bin/a b",
                "/usr/bin/$(x)", "../git", "/" + "a" * 600):
        with pytest.raises(ValueError):
            programs.custom_key(bad)


def test_custom_program_add_policy_and_delete(client, parent):
    cid, tok = _setup(client, parent)
    with device(client, tok, "pc", map_to=cid) as (ws, dev_id):
        _policy(ws)
        page = client.get(f"/children/{cid}").text
        assert "Programm hinzufügen" in page and 'id="add-program"' in page

        r = client.post(f"/children/{cid}/programs/custom", data={"csrf_token": tok, "path": "git", "name": "Git"},
                        follow_redirects=False)
        assert r.status_code == 303
        assert _policy(ws).children[0].allowed_programs == ["exe:/usr/bin/git"]
        page = client.get(f"/children/{cid}").text
        assert "Eigene Programme" in page and "Git" in page and "/usr/bin/git" in page

        # Abwählen in der Liste: Eintrag bleibt, Freigabe weg
        client.post(f"/children/{cid}/programs", data={"csrf_token": tok, "program_mode": "enforce"})
        assert _policy(ws).children[0].allowed_programs == []
        assert "Eigene Programme" in client.get(f"/children/{cid}").text

        # Ungültige Eingabe: Fehlermeldung im (offenen) Dialog, nichts gespeichert
        r = client.post(f"/children/{cid}/programs/custom", data={"csrf_token": tok, "path": "/usr/bin/../x"})
        assert r.status_code == 400 and "absoluten Programmpfad" in r.text and 'class="dialog" open>' in r.text

        # Entfernen
        from app.db import SessionLocal
        from app.models import ChildCustomProgram
        with SessionLocal() as db:
            custom_id = db.query(ChildCustomProgram).one().id
        client.post(f"/children/{cid}/programs/custom", data={"csrf_token": tok, "path": "/usr/bin/git"})
        assert _policy(ws).children[0].allowed_programs == ["exe:/usr/bin/git"]
        r = client.post(f"/children/{cid}/programs/custom/{custom_id}/delete", data={"csrf_token": tok},
                        follow_redirects=False)
        assert r.status_code == 303
        assert _policy(ws).children[0].allowed_programs == []
        assert "Eigene Programme" not in client.get(f"/children/{cid}").text
        assert client.post(f"/children/{cid}/programs/custom/{custom_id}/delete",
                           data={"csrf_token": tok}).status_code == 404
        # ohne CSRF abgelehnt
        assert client.post(f"/children/{cid}/programs/custom", data={"path": "git"}).status_code in (400, 403)
