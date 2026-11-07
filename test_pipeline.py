"""Test the economic, quality and replay invariants with isolated fixtures."""
from datetime import date

import pytest

import pipeline as p


@pytest.fixture
def initialized(tmp_path):
    p.initialize(p.synthetic(),p.DEMO_DAY,tmp_path)
    return tmp_path


def race(folder, **changes):
    employee=p.metrics(folder)["settings"]["demo_employee"]
    return dict(id="testcourse",employee_id=employee,sport="Course à pied",begins="2026-11-07T11:00",
                ends="2026-11-07T11:32",distance_m=5000,comment="") | changes


def test_window_and_seed(initialized):
    a=p.metrics(initialized)
    assert a["kpi"]["employees"]==60
    assert a["kpi"]["activities"]>2000
    assert a["kpi"]["demo_count"]==14
    with p.connect(initialized) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall()==[]
        assert db.execute("SELECT count(*) FROM activity WHERE begins<'2025-11-08' OR begins>='2026-11-08'").fetchone()[0]==0


def test_live_run_crosses_threshold_and_replay_is_idempotent(initialized):
    before=p.metrics(initialized)
    payload=race(initialized)
    for _ in range(2):
        p.enqueue(payload,initialized)
        p.consume(initialized)
    after=p.metrics(initialized)
    assert after["kpi"]["activities"]==before["kpi"]["activities"]+1
    assert after["kpi"]["days"]==before["kpi"]["days"]+5
    assert after["kpi"]["demo_count"]==15
    assert len(after["outbox"])==1
    assert "5.0 km" in after["outbox"][0]["text"]
    assert any(row["status"]=="rejeu" for row in after["runs"])
    p.initialize(p.synthetic(),p.DEMO_DAY,initialized)
    assert p.metrics(initialized)["kpi"]==after["kpi"]


@pytest.mark.parametrize("changes",[
    {"distance_m":-1},{"begins":"2025-11-07T11:00"},{"ends":"2026-11-07T10:00"},
    {"employee_id":"inconnu"},{"distance_m":None},
])
def test_bad_activity_does_not_change_reporting(initialized,changes):
    before=p.metrics(initialized)["kpi"]
    p.enqueue(race(initialized,**changes),initialized)
    p.consume(initialized)
    after=p.metrics(initialized)
    assert after["kpi"]==before and not after["outbox"]
    assert any(row["status"]=="rejet" for row in after["runs"])


def test_recalculates_money_with_config_audit(initialized):
    previous=p.metrics(initialized)["kpi"]
    p.change_config(6,15,5,initialized)
    updated=p.metrics(initialized)["kpi"]
    assert updated["cost_cents"] * 5==previous["cost_cents"] * 6
    assert updated["activities"]==previous["activities"]
    with p.connect(initialized) as db:
        assert db.execute("SELECT rate FROM config_history ORDER BY id").fetchall()[1][0]=="6"


def test_slack_payload_and_duplicate_guard(initialized,monkeypatch):
    p.enqueue(race(initialized,comment="Information privée"),initialized)
    p.consume(initialized)
    sent=[]
    class MockResponse:
        status_code=200
        text="ok"
    monkeypatch.setenv("SLACK_WEBHOOK_URL","https://hooks.slack.com/services/demo/demo/demo")
    def post(url, **args):
        sent.append(args["json"])
        return MockResponse()
    monkeypatch.setattr(p.requests,"post",post)
    assert p.deliver_slack("testcourse",initialized)=="livre"
    assert len(sent)==1 and "Information privée" not in sent[0]["text"]
    with pytest.raises(ValueError,match="déjà"):
        p.deliver_slack("testcourse",initialized)


def test_google_travel_modes_without_network(monkeypatch):
    seen=[]
    class MockResponse:
        status_code=200
        def json(self): return {"routes":[{"distanceMeters":12000}]}
    def post(url,**args):
        seen.append(args["json"]["travelMode"])
        return MockResponse()
    monkeypatch.setattr(p.requests,"post",post)
    assert p.google_distance("Adresse fictive","marche/running","fake")==12000
    assert p.google_distance("Adresse fictive","velo/trottinette/autres","fake")==12000
    assert seen==["WALK","BICYCLE"]
