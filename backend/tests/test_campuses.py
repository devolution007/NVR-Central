from app.models.enums import Role
from tests.conftest import Api, make_campus, make_user


def test_admin_campus_crud(admin_api: Api) -> None:
    r = admin_api.post("/api/v1/campuses", json={"name": "  North  ", "description": "d", "location": "Lahore"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert r.json()["name"] == "North"  # trimmed
    assert admin_api.get(f"/api/v1/campuses/{cid}").json()["location"] == "Lahore"

    r = admin_api.put(f"/api/v1/campuses/{cid}", json={"name": "North Campus", "description": "", "location": "Lahore"})
    assert r.status_code == 200 and r.json()["name"] == "North Campus"

    page = admin_api.get("/api/v1/campuses").json()
    assert page["total"] == 1 and page["items"][0]["nvr_count"] == 0

    assert admin_api.delete(f"/api/v1/campuses/{cid}").status_code == 204
    assert admin_api.get(f"/api/v1/campuses/{cid}").status_code == 404
    assert admin_api.get("/api/v1/campuses").json()["total"] == 0


def test_duplicate_name_conflict_is_case_insensitive_but_deleted_names_are_reusable(admin_api: Api) -> None:
    cid = admin_api.post("/api/v1/campuses", json={"name": "East"}).json()["id"]
    r = admin_api.post("/api/v1/campuses", json={"name": "EAST"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "campus_name_taken"
    admin_api.delete(f"/api/v1/campuses/{cid}")
    assert admin_api.post("/api/v1/campuses", json={"name": "East"}).status_code == 201


def test_blank_name_rejected_with_field_details(admin_api: Api) -> None:
    r = admin_api.post("/api/v1/campuses", json={"name": "   "})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["details"][0]["field"] == "name"


def test_only_admin_can_modify_campuses(api: Api, db) -> None:
    c = make_campus(db, "C1")
    make_user(db, "op", Role.OPERATOR, [c.id])
    make_user(db, "vw", Role.VIEWER, [c.id])
    for name in ("op", "vw"):
        a = Api()
        a.login(name)
        assert a.post("/api/v1/campuses", json={"name": "X"}).status_code == 403
        assert a.put(f"/api/v1/campuses/{c.id}", json={"name": "Y"}).status_code == 403
        assert a.delete(f"/api/v1/campuses/{c.id}").status_code == 403


def test_non_admin_sees_only_assigned_campuses(db) -> None:
    c1, c2 = make_campus(db, "Visible"), make_campus(db, "Hidden")
    make_user(db, "vw", Role.VIEWER, [c1.id])
    a = Api()
    a.login("vw")
    items = a.get("/api/v1/campuses").json()["items"]
    assert [i["name"] for i in items] == ["Visible"]
    assert a.get(f"/api/v1/campuses/{c1.id}").status_code == 200
    # a campus the user cannot see looks exactly like one that does not exist
    hidden = a.get(f"/api/v1/campuses/{c2.id}")
    assert hidden.status_code == 404


def test_cannot_delete_campus_that_still_has_nvrs(admin_api: Api) -> None:
    cid = admin_api.post("/api/v1/campuses", json={"name": "WithNvr"}).json()["id"]
    nvr = admin_api.post(
        "/api/v1/nvrs",
        json={
            "campus_id": cid,
            "name": "N1",
            "connection_type": "lan",
            "host": "192.168.1.10",
            "username": "admin",
            "password": "pw",
        },
    )
    assert nvr.status_code == 201
    r = admin_api.delete(f"/api/v1/campuses/{cid}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "campus_has_nvrs"
    assert admin_api.get("/api/v1/campuses").json()["items"][0]["nvr_count"] == 1
    admin_api.delete(f"/api/v1/nvrs/{nvr.json()['id']}")
    assert admin_api.delete(f"/api/v1/campuses/{cid}").status_code == 204


def test_search_and_pagination(admin_api: Api) -> None:
    for n in ("Alpha", "Beta", "Gamma", "Alps"):
        admin_api.post("/api/v1/campuses", json={"name": n})
    assert admin_api.get("/api/v1/campuses?q=al").json()["total"] == 2
    page = admin_api.get("/api/v1/campuses?page=2&page_size=3").json()
    assert page["total"] == 4 and len(page["items"]) == 1 and page["page"] == 2


def test_unauthenticated_access_denied(api: Api) -> None:
    assert api.get("/api/v1/campuses").status_code == 401
