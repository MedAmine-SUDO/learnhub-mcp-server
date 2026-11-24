"""A second fictional LMS API, test-only, with a deliberately different contract.

API key header instead of OAuth, offset/count pagination, `results`/`count` envelopes,
camelCase fields, records wrapped in `data`, and other words for roles and statuses.
Same people and trainings as the bundled mock, so answers can be compared.
"""

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from mock_api.data import DATASETS

KEYS = {"acme-key": 1, "globex-key": 2}
ROLE = {"learner": "student", "instructor": "teacher", "admin": "admin"}
STATUS = {"active": "enabled", "inactive": "disabled"}
COURSE = {"published": "live", "draft": "draft", "archived": "retired"}
PROGRESS = {"not_started": "todo", "in_progress": "doing", "completed": "done"}

app = FastAPI(title="Brightpath (fictional)")


def company(x_api_key: str = Header("")) -> int:
    if x_api_key not in KEYS:
        raise HTTPException(401, "bad key")
    return KEYS[x_api_key]


def person(u: dict) -> dict:
    return {
        "personId": u["id"], "givenName": u["first_name"], "familyName": u["last_name"],
        "mail": u["email"], "kind": ROLE[u["role"]], "state": STATUS[u["status"]],
        "team": u["department"],
    }


def course(t: dict) -> dict:
    return {
        "courseId": t["id"], "name": t["title"], "topic": t["category"],
        "state": COURSE[t["status"]], "hours": t["duration_hours"],
    }


def window(items: list, start: int, count: int) -> dict:
    return {"results": items[start : start + count], "count": len(items)}


@app.get("/api/v2/people")
def people(
    cid: int = Depends(company), start: int = Query(0, ge=0), count: int = Query(20, le=50),
    kind: str | None = None, state: str | None = None, q: str | None = None,
):
    rows = [person(u) for u in DATASETS[cid]["users"]]
    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    if state:
        rows = [r for r in rows if r["state"] == state]
    if q:
        rows = [r for r in rows if q.lower() in f"{r['givenName']} {r['familyName']} {r['mail']}".lower()]
    return window(rows, start, count)


@app.get("/api/v2/people/{pid}")
def one_person(pid: int, cid: int = Depends(company)):
    for u in DATASETS[cid]["users"]:
        if u["id"] == pid:
            return {"data": person(u)}
    raise HTTPException(404, "no such person")


@app.get("/api/v2/courses")
def courses(
    cid: int = Depends(company), start: int = Query(0, ge=0), count: int = Query(20, le=50),
    state: str | None = None, topic: str | None = None,
):
    rows = [course(t) for t in DATASETS[cid]["trainings"]]
    if state:
        rows = [r for r in rows if r["state"] == state]
    if topic:
        rows = [r for r in rows if r["topic"].lower() == topic.lower()]
    return window(rows, start, count)


@app.get("/api/v2/courses/{course_id}")
def one_course(course_id: int, cid: int = Depends(company)):
    for t in DATASETS[cid]["trainings"]:
        if t["id"] == course_id:
            return {"data": course(t)}
    raise HTTPException(404, "no such course")


@app.get("/api/v2/courses/{course_id}/enrollments")
def enrollments(
    course_id: int, cid: int = Depends(company), start: int = Query(0, ge=0),
    count: int = Query(20, le=50), progress: str | None = None,
):
    one_course(course_id, cid)
    rows = [
        {"personId": e["user_id"], "courseId": e["training_id"], "percent": e["progress_pct"],
         "progress": PROGRESS[e["completion"]], "grade": e["score"]}
        for e in DATASETS[cid]["enrollments"] if e["training_id"] == course_id
    ]
    if progress:
        rows = [r for r in rows if r["progress"] == progress]
    return window(rows, start, count)
