"""Votes on a date: groups, settings, choices, results visibility, and setting the final date."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Notification
from tests.helpers import create_section, enroll, make_actor


def future(days: int, hour: int = 10) -> str:
    moment = datetime.now(timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days)
    return moment.isoformat()


def create_vote(client, professor, sections, dates=(3, 4, 5), **settings):
    response = client.post(
        "/api/v1/votes",
        json={
            "title": "Databases exam",
            "section_ids": list(sections),
            "options": [{"starts_at": future(days), "note": f"Room {days}"} for days in dates],
            **settings,
        },
        headers=professor["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


def choose(client, student, vote, *indexes):
    option_ids = [vote["options"][index]["id"] for index in indexes]
    return client.put(f"/api/v1/votes/{vote['id']}/choice", json={"option_ids": option_ids}, headers=student["headers"])


def view(client, actor, vote):
    response = client.get(f"/api/v1/votes/{vote['id']}", headers=actor["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def close(client, professor, vote, **body):
    return client.post(f"/api/v1/votes/{vote['id']}/close", json=body, headers=professor["headers"])


@pytest.fixture
def second_section(client, professor):
    return create_section(client, professor, "Databases, group 2")


@pytest.fixture
def second_group_student(client, professor, second_section):
    actor = make_actor(client, "group2@example.com", "student")
    enroll(client, professor, second_section, actor)
    return actor


# ---------- Setting the groups ----------


def test_students_of_every_chosen_group_can_vote_once(client, professor, section, second_section, student, second_group_student):
    enroll(client, professor, second_section, student)  # in both groups, still one voter
    vote = create_vote(client, professor, [section, second_section])
    assert {group["name"] for group in vote["groups"]} == {"Databases", "Databases, group 2"}
    assert vote["eligible"] == 2
    assert choose(client, student, vote, 0).status_code == 200
    assert choose(client, second_group_student, vote, 1).status_code == 200
    assert view(client, professor, vote)["voted"] == 2


def test_students_outside_the_groups_do_not_see_the_vote(client, professor, section, outsider):
    vote = create_vote(client, professor, [section])
    assert client.get("/api/v1/votes", headers=outsider["headers"]).json() == []
    assert client.get(f"/api/v1/votes/{vote['id']}", headers=outsider["headers"]).status_code == 404
    assert choose(client, outsider, vote, 0).status_code == 404


def test_a_vote_can_only_target_the_professors_own_classes(client, professor, other_professor):
    foreign = create_section(client, other_professor, "Networks")
    response = client.post(
        "/api/v1/votes",
        json={"title": "Exam", "section_ids": [foreign], "options": [{"starts_at": future(2)}, {"starts_at": future(3)}]},
        headers=professor["headers"],
    )
    assert response.status_code == 404


def test_another_professor_cannot_see_or_close_the_vote(client, professor, other_professor, section):
    vote = create_vote(client, professor, [section])
    assert client.get(f"/api/v1/votes/{vote['id']}", headers=other_professor["headers"]).status_code == 404
    assert close(client, other_professor, vote).status_code == 404
    assert client.get("/api/v1/votes", headers=other_professor["headers"]).json() == []


def test_students_get_a_notification_when_a_vote_starts(client, professor, section, student, db):
    create_vote(client, professor, [section])
    titles = [n.title for n in db.query(Notification).filter_by(user_id=student["id"]).all()]
    assert titles == ["New vote: Databases exam"]


# ---------- Setting the dates ----------


@pytest.mark.parametrize(
    "dates, detail",
    [
        ([future(-1), future(2)], "Every date must be in the future"),
        ([future(2), future(2)], "The same date is offered twice"),
    ],
)
def test_dates_must_be_future_and_different(client, professor, section, dates, detail):
    response = client.post(
        "/api/v1/votes",
        json={"title": "Exam", "section_ids": [section], "options": [{"starts_at": d} for d in dates]},
        headers=professor["headers"],
    )
    assert response.status_code == 422
    assert response.json()["detail"] == detail


def test_a_vote_needs_at_least_two_dates(client, professor, section):
    response = client.post(
        "/api/v1/votes",
        json={"title": "Exam", "section_ids": [section], "options": [{"starts_at": future(2)}]},
        headers=professor["headers"],
    )
    assert response.status_code == 422


def test_options_come_back_sorted_by_date_in_utc(client, professor, section):
    vote = create_vote(client, professor, [section], dates=(5, 3, 4))
    starts = [option["starts_at"] for option in vote["options"]]
    assert starts == sorted(starts)
    assert all(start.endswith("+00:00") or start.endswith("Z") for start in starts)


# ---------- Setting the system ----------


def test_single_choice_vote_takes_exactly_one_date(client, professor, section, student):
    vote = create_vote(client, professor, [section])
    response = choose(client, student, vote, 0, 1)
    assert response.status_code == 422
    assert response.json()["detail"] == "Pick one date"


def test_multiple_choice_vote_takes_several_dates(client, professor, section, student):
    vote = create_vote(client, professor, [section], allow_multiple=True)
    body = choose(client, student, vote, 0, 2).json()
    assert set(body["my_choice"]) == {vote["options"][0]["id"], vote["options"][2]["id"]}


def test_a_student_can_change_their_choice_until_the_vote_closes(client, professor, section, student):
    vote = create_vote(client, professor, [section])
    choose(client, student, vote, 0)
    assert choose(client, student, vote, 2).json()["my_choice"] == [vote["options"][2]["id"]]
    counts = [option["votes"] for option in view(client, professor, vote)["options"]]
    assert counts == [0, 0, 1]
    close(client, professor, vote)
    response = choose(client, student, vote, 1)
    assert response.status_code == 409


def test_options_of_another_vote_are_refused(client, professor, section, student):
    first = create_vote(client, professor, [section])
    second = create_vote(client, professor, [section], dates=(7, 8))
    response = client.put(
        f"/api/v1/votes/{first['id']}/choice", json={"option_ids": [second["options"][0]["id"]]}, headers=student["headers"]
    )
    assert response.status_code == 422


def test_named_vote_shows_the_professor_who_picked_what(client, professor, section, student, other_student):
    vote = create_vote(client, professor, [section])
    choose(client, student, vote, 1)
    result = view(client, professor, vote)
    assert [voter["id"] for voter in result["options"][1]["voters"]] == [student["id"]]
    assert [person["id"] for person in result["not_voted"]] == [other_student["id"]]


def test_anonymous_vote_shows_only_totals(client, professor, section, student, other_student):
    vote = create_vote(client, professor, [section], anonymous=True)
    choose(client, student, vote, 1)
    result = view(client, professor, vote)
    assert result["options"][1]["votes"] == 1
    assert all(option["voters"] is None for option in result["options"])
    # who has not answered yet is still shown, which choice a student made is not
    assert [person["id"] for person in result["not_voted"]] == [other_student["id"]]


def test_live_results_show_once_the_student_has_voted(client, professor, section, student, other_student):
    vote = create_vote(client, professor, [section], results_visibility="live")
    choose(client, other_student, vote, 0)
    before = view(client, student, vote)
    assert before["results_visible"] is False
    assert before["options"][0]["votes"] is None
    after = choose(client, student, vote, 0).json()
    assert after["results_visible"] is True
    assert after["options"][0]["votes"] == 2
    assert after["not_voted"] is None
    assert all(option["voters"] is None for option in after["options"])


def test_results_after_close_stay_hidden_while_open(client, professor, section, student):
    vote = create_vote(client, professor, [section], results_visibility="after_close")
    assert choose(client, student, vote, 0).json()["results_visible"] is False
    close(client, professor, vote)
    assert view(client, student, vote)["options"][0]["votes"] == 1


def test_professor_only_results_never_reach_students(client, professor, section, student):
    vote = create_vote(client, professor, [section], results_visibility="professor_only")
    choose(client, student, vote, 0)
    close(client, professor, vote)
    result = view(client, student, vote)
    assert result["results_visible"] is False
    assert result["options"][0]["votes"] is None
    # the date that was set is always shown
    assert result["chosen_option_id"] == vote["options"][0]["id"]


# ---------- Setting the final date ----------


def test_closing_takes_the_most_voted_date(client, professor, section, student, other_student, db):
    vote = create_vote(client, professor, [section])
    choose(client, student, vote, 2)
    choose(client, other_student, vote, 2)
    closed = close(client, professor, vote).json()
    assert closed["status"] == "closed"
    assert closed["chosen_option_id"] == vote["options"][2]["id"]
    messages = [n.message for n in db.query(Notification).filter_by(user_id=student["id"], title="Date set: Databases exam")]
    assert len(messages) == 1
    assert "(Room 5)" in messages[0]


def test_a_tie_takes_the_earliest_date(client, professor, section, student, other_student):
    vote = create_vote(client, professor, [section])
    choose(client, student, vote, 2)
    choose(client, other_student, vote, 1)
    assert close(client, professor, vote).json()["chosen_option_id"] == vote["options"][1]["id"]


def test_the_professor_can_set_another_date_than_the_winner(client, professor, section, student):
    vote = create_vote(client, professor, [section])
    choose(client, student, vote, 2)
    assert close(client, professor, vote, option_id=vote["options"][0]["id"]).json()["chosen_option_id"] == vote["options"][0]["id"]


def test_closing_twice_or_with_a_foreign_date_is_refused(client, professor, section):
    vote = create_vote(client, professor, [section])
    assert close(client, professor, vote, option_id="not-an-option").status_code == 422
    assert close(client, professor, vote).status_code == 200
    assert close(client, professor, vote).status_code == 409


def test_a_student_removed_from_the_class_no_longer_counts(client, professor, section, student, other_student):
    vote = create_vote(client, professor, [section])
    choose(client, student, vote, 0)
    client.delete(f"/api/v1/sections/{section}/enrollments/{student['id']}", headers=professor["headers"])
    result = view(client, professor, vote)
    assert result["eligible"] == 1
    assert result["options"][0]["votes"] == 0


def test_deleting_an_open_vote_tells_the_students(client, professor, section, student, db):
    vote = create_vote(client, professor, [section])
    choose(client, student, vote, 0)
    assert client.delete(f"/api/v1/votes/{vote['id']}", headers=professor["headers"]).status_code == 204
    assert client.get("/api/v1/votes", headers=student["headers"]).json() == []
    assert db.query(Notification).filter_by(user_id=student["id"], title="Vote cancelled: Databases exam").count() == 1


def test_students_cannot_start_or_close_votes(client, professor, section, student):
    vote = create_vote(client, professor, [section])
    response = client.post(
        "/api/v1/votes",
        json={"title": "Exam", "section_ids": [section], "options": [{"starts_at": future(2)}, {"starts_at": future(3)}]},
        headers=student["headers"],
    )
    assert response.status_code == 403
    assert close(client, student, vote).status_code == 403
