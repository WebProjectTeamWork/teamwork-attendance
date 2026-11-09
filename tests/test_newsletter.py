"""Newsletter: who can write to whom, where replies go, and who can read what."""

import pytest

from app.config import get_settings
from app.models import AuditLog, Notification
from tests.helpers import create_section, enroll


def post(client, sender, **body):
    payload = {"subject": "Exam room", "body": "The exam moved to room 204.", **body}
    return client.post("/api/v1/messages", json=payload, headers=sender["headers"])


def send(client, sender, **body) -> dict:
    response = post(client, sender, **body)
    assert response.status_code == 201, response.text
    return response.json()


def reply(client, sender, message_id, body="Thank you."):
    return client.post(f"/api/v1/messages/{message_id}/reply", json={"body": body}, headers=sender["headers"])


def box(client, actor, name="inbox") -> list[dict]:
    response = client.get(f"/api/v1/messages/{name}", headers=actor["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def thread(client, actor, thread_id):
    return client.get(f"/api/v1/messages/threads/{thread_id}", headers=actor["headers"])


def unread(client, actor) -> int:
    return client.get("/api/v1/messages/unread-count", headers=actor["headers"]).json()["unread"]


def mark_read(client, actor, thread_id):
    return client.post(f"/api/v1/messages/threads/{thread_id}/read", headers=actor["headers"])


def test_professor_writes_to_a_student(client, professor, student):
    sent = send(client, professor, recipient_id=student["id"])
    assert sent["recipient_count"] == 1
    [summary] = box(client, student)
    assert summary["id"] == sent["thread_id"]
    assert summary["subject"] == "Exam room"
    assert summary["counterpart"] == "Test Professor"
    assert summary["unread"] == 1 and summary["is_group"] is False
    assert box(client, professor) == []
    assert box(client, professor, "sent")[0]["counterpart"] == "Test Student"


def test_student_writes_to_their_professor(client, professor, student):
    sent = send(client, student, recipient_id=professor["id"])
    assert box(client, professor)[0]["id"] == sent["thread_id"]


def test_direct_conversation_goes_back_and_forth(client, professor, student):
    first = send(client, student, recipient_id=professor["id"])
    answer = reply(client, professor, first["id"], "Yes, come at noon.")
    assert answer.status_code == 201
    assert reply(client, student, answer.json()["id"], "See you then.").status_code == 201
    bodies = [message["body"] for message in thread(client, professor, first["thread_id"]).json()["messages"]]
    assert bodies == ["The exam moved to room 204.", "Yes, come at noon.", "See you then."]
    assert box(client, professor)[0]["message_count"] == 3


def test_following_up_your_own_direct_message_reaches_the_same_person(client, professor, student):
    first = send(client, student, recipient_id=professor["id"])
    assert reply(client, student, first["id"], "One more thing.").status_code == 201
    assert unread(client, professor) == 2


def test_group_post_reaches_every_active_student(client, professor, section, student, other_student):
    sent = send(client, professor, section_ids=[section])
    assert sent["recipient_count"] == 2
    for actor in (student, other_student):
        [summary] = box(client, actor)
        assert summary["is_group"] is True
        assert summary["counterpart"] == "Test Professor"


def test_post_to_two_groups_reaches_a_shared_student_once(client, professor, section, student):
    second = create_section(client, professor, "Algorithms")
    enroll(client, professor, second, student)
    sent = send(client, professor, section_ids=[section, second, section])
    assert sent["recipient_count"] == 1
    assert unread(client, student) == 1
    assert box(client, professor, "sent")[0]["counterpart"] == "Databases, Algorithms"


def test_removed_student_is_left_out_of_group_posts(client, professor, section, student, other_student):
    removed = client.delete(f"/api/v1/sections/{section}/enrollments/{other_student['id']}", headers=professor["headers"])
    assert removed.status_code == 204
    assert send(client, professor, section_ids=[section])["recipient_count"] == 1
    assert box(client, other_student) == []


def test_student_who_joins_later_does_not_see_earlier_posts(client, professor, section, student, outsider):
    send(client, professor, section_ids=[section])
    enroll(client, professor, section, outsider)
    assert box(client, outsider) == []


def test_group_reply_goes_only_to_the_author(client, professor, section, student, other_student):
    post_sent = send(client, professor, section_ids=[section])
    assert reply(client, student, post_sent["id"], "Which floor?").status_code == 201
    assert unread(client, professor) == 1
    assert unread(client, other_student) == 1  # only the original post
    seen = thread(client, other_student, post_sent["thread_id"]).json()["messages"]
    assert [message["body"] for message in seen] == ["The exam moved to room 204."]


def test_professor_answer_to_a_group_reply_is_private(client, professor, section, student, other_student):
    post_sent = send(client, professor, section_ids=[section])
    question = reply(client, student, post_sent["id"], "Which floor?").json()
    assert reply(client, professor, question["id"], "Second floor.").status_code == 201
    assert len(thread(client, student, post_sent["thread_id"]).json()["messages"]) == 3
    assert len(thread(client, other_student, post_sent["thread_id"]).json()["messages"]) == 1


def test_professor_can_follow_up_to_the_whole_group(client, professor, section, student, other_student):
    post_sent = send(client, professor, section_ids=[section])
    update = reply(client, professor, post_sent["id"], "Bring your ID.")
    assert update.json()["recipient_count"] == 2
    assert len(thread(client, other_student, post_sent["thread_id"]).json()["messages"]) == 2


@pytest.mark.parametrize("sender_name", ["professor", "student"])
def test_cannot_write_to_someone_outside_your_classes(client, professor, student, outsider, sender_name):
    sender = {"professor": professor, "student": student}[sender_name]
    assert post(client, sender, recipient_id=outsider["id"]).status_code == 403


def test_outsider_cannot_write_to_the_professor(client, professor, section, outsider):
    assert post(client, outsider, recipient_id=professor["id"]).status_code == 403


def test_students_cannot_write_to_each_other(client, student, other_student):
    assert post(client, student, recipient_id=other_student["id"]).status_code == 403


def test_student_cannot_post_to_a_group(client, section, student):
    assert post(client, student, section_ids=[section]).status_code == 403


def test_professor_cannot_post_to_another_professors_group(client, other_professor, section):
    assert post(client, other_professor, section_ids=[section]).status_code == 404


def test_group_without_students_is_refused(client, professor, section):
    assert post(client, professor, section_ids=[section]).status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        {"subject": "   "},
        {"body": "   "},
        {"subject": "x" * 151},
        {"body": "x" * 5001},
    ],
)
def test_invalid_content_is_refused(client, professor, student, change):
    assert post(client, professor, recipient_id=student["id"], **change).status_code == 422


def test_needs_exactly_one_audience(client, professor, section, student):
    assert post(client, professor).status_code == 422
    assert post(client, professor, recipient_id=student["id"], section_ids=[section]).status_code == 422


def test_requires_sign_in(client):
    assert client.get("/api/v1/messages/inbox").status_code == 401
    assert client.post("/api/v1/messages", json={}).status_code == 401


def test_strangers_cannot_read_or_answer_a_thread(client, professor, student, outsider):
    sent = send(client, professor, recipient_id=student["id"])
    assert thread(client, outsider, sent["thread_id"]).status_code == 404
    assert mark_read(client, outsider, sent["thread_id"]).status_code == 404
    assert reply(client, outsider, sent["id"]).status_code == 404


def test_student_removed_from_class_can_read_but_not_reply(client, professor, section, student):
    sent = send(client, professor, recipient_id=student["id"])
    client.delete(f"/api/v1/sections/{section}/enrollments/{student['id']}", headers=professor["headers"])
    detail = thread(client, student, sent["thread_id"])
    assert detail.status_code == 200
    assert detail.json()["can_reply"] is False
    assert reply(client, student, sent["id"]).status_code == 403


def test_read_state_is_per_person(client, professor, section, student, other_student):
    sent = send(client, professor, section_ids=[section])
    assert mark_read(client, student, sent["thread_id"]).status_code == 204
    assert unread(client, student) == 0
    assert unread(client, other_student) == 1
    assert box(client, student)[0]["unread"] == 0


def test_author_sees_how_many_read_a_group_post(client, professor, section, student, other_student):
    sent = send(client, professor, section_ids=[section])
    mark_read(client, student, sent["thread_id"])
    author_view = thread(client, professor, sent["thread_id"]).json()["messages"][0]
    assert (author_view["read_count"], author_view["recipient_count"]) == (1, 2)
    student_view = thread(client, student, sent["thread_id"]).json()["messages"][0]
    assert student_view["read_count"] is None and student_view["recipient_count"] is None


def test_recipients_get_a_notification_without_the_body(client, db, professor, student):
    send(client, professor, recipient_id=student["id"])
    [note] = client.get("/api/v1/notifications", headers=student["headers"]).json()
    assert note["title"] == "New message from Test Professor"
    assert note["message"] == "Exam room"
    assert db.query(Notification).count() == 1


def test_reading_the_bell_does_not_mark_messages_read(client, professor, student):
    send(client, professor, recipient_id=student["id"])
    client.post("/api/v1/notifications/read", headers=student["headers"])
    assert unread(client, student) == 1


def test_group_post_is_audited(client, db, professor, section, student):
    sent = send(client, professor, section_ids=[section])
    entry = db.query(AuditLog).filter_by(action="group_message_sent").one()
    assert entry.actor_id == professor["id"] and entry.entity_id == sent["id"]


def test_markup_is_stored_as_plain_text(client, professor, student):
    body = "<img src=x onerror=alert(1)>"
    sent = send(client, professor, recipient_id=student["id"], body=body)
    assert thread(client, student, sent["thread_id"]).json()["messages"][0]["body"] == body


def test_contacts_for_a_professor(client, professor, section, student):
    body = client.get("/api/v1/messages/contacts", headers=professor["headers"]).json()
    assert [person["id"] for person in body["people"]] == [student["id"]]
    assert body["people"][0]["classes"] == ["Databases"]
    assert body["groups"] == [{"id": section, "name": "Databases", "student_ids": [student["id"]]}]


def test_contacts_for_a_student(client, professor, student, other_student):
    body = client.get("/api/v1/messages/contacts", headers=student["headers"]).json()
    assert [person["id"] for person in body["people"]] == [professor["id"]]
    assert body["groups"] == []


def test_contacts_leave_out_inactive_accounts(client, db, professor, student):
    from app.models import User

    db.get(User, student["id"]).is_active = False
    db.commit()
    assert client.get("/api/v1/messages/contacts", headers=professor["headers"]).json()["people"] == []


def test_inbox_lists_the_newest_conversation_first(client, professor, student):
    older = send(client, professor, recipient_id=student["id"], subject="Older")
    send(client, professor, recipient_id=student["id"], subject="Newer")
    assert [summary["subject"] for summary in box(client, student)] == ["Newer", "Older"]
    reply(client, professor, older["id"], "Bumped.")
    assert [summary["subject"] for summary in box(client, student)] == ["Older", "Newer"]


def test_sending_is_rate_limited(client, professor, student, monkeypatch):
    monkeypatch.setattr(get_settings(), "message_limit_per_user", 2)
    send(client, professor, recipient_id=student["id"])
    send(client, professor, recipient_id=student["id"])
    assert post(client, professor, recipient_id=student["id"]).status_code == 429


# Subscriptions


def make_newsletter(client, professor, name="Erasmus news") -> str:
    response = client.post("/api/v1/newsletters", json={"name": name, "description": "Exchange programmes"}, headers=professor["headers"])
    assert response.status_code == 201, response.text
    return response.json()["id"]


def follow(client, student, kind, target_id):
    return client.put(f"/api/v1/subscriptions/{kind}/{target_id}", headers=student["headers"])


def unfollow(client, student, kind, target_id):
    return client.delete(f"/api/v1/subscriptions/{kind}/{target_id}", headers=student["headers"])


def directory(client, student) -> dict:
    return client.get("/api/v1/subscriptions", headers=student["headers"]).json()


def test_group_subscriber_receives_group_posts(client, professor, section, student, outsider):
    assert follow(client, outsider, "groups", section).status_code == 204
    assert send(client, professor, section_ids=[section])["recipient_count"] == 2
    assert box(client, outsider)[0]["is_group"] is True


def test_group_subscription_is_not_enrollment(client, professor, section, outsider):
    follow(client, outsider, "groups", section)
    assert client.get("/api/v1/sections", headers=outsider["headers"]).json() == []
    assert client.get(f"/api/v1/sections/{section}/students", headers=professor["headers"]).json() == []
    assert client.get("/api/v1/students/me/statistics", headers=outsider["headers"]).json()["total_sessions"] == 0


def test_enrolled_student_cannot_subscribe_to_their_own_group(client, section, student):
    assert follow(client, student, "groups", section).status_code == 409


def test_unsubscribing_stops_future_posts_and_keeps_old_ones(client, professor, section, student, outsider):
    follow(client, outsider, "groups", section)
    send(client, professor, section_ids=[section], subject="First")
    assert unfollow(client, outsider, "groups", section).status_code == 204
    send(client, professor, section_ids=[section], subject="Second")
    assert [summary["subject"] for summary in box(client, outsider)] == ["First"]


def test_subscribing_twice_is_harmless(client, professor, section, student, outsider):
    assert follow(client, outsider, "groups", section).status_code == 204
    assert follow(client, outsider, "groups", section).status_code == 204
    assert send(client, professor, section_ids=[section])["recipient_count"] == 2


def test_newsletter_reaches_only_its_subscribers(client, professor, student, other_student):
    newsletter = make_newsletter(client, professor)
    follow(client, student, "newsletters", newsletter)
    assert send(client, professor, newsletter_ids=[newsletter])["recipient_count"] == 1
    assert unread(client, student) == 1
    assert unread(client, other_student) == 0
    assert box(client, professor, "sent")[0]["counterpart"] == "Erasmus news"


def test_student_in_group_and_newsletter_gets_one_copy(client, professor, section, student):
    newsletter = make_newsletter(client, professor)
    follow(client, student, "newsletters", newsletter)
    assert send(client, professor, section_ids=[section], newsletter_ids=[newsletter])["recipient_count"] == 1
    assert unread(client, student) == 1


def test_newsletter_without_subscribers_is_refused(client, professor):
    assert post(client, professor, newsletter_ids=[make_newsletter(client, professor)]).status_code == 422


def test_professor_cannot_post_to_another_professors_newsletter(client, professor, other_professor):
    assert post(client, other_professor, newsletter_ids=[make_newsletter(client, professor)]).status_code == 404


def test_student_cannot_post_to_a_newsletter(client, professor, student):
    assert post(client, student, newsletter_ids=[make_newsletter(client, professor)]).status_code == 403


def test_subscriber_can_reply_and_get_an_answer(client, professor, outsider):
    newsletter = make_newsletter(client, professor)
    follow(client, outsider, "newsletters", newsletter)
    issue = send(client, professor, newsletter_ids=[newsletter])
    assert thread(client, outsider, issue["thread_id"]).json()["can_reply"] is True
    question = reply(client, outsider, issue["id"], "Is Spain included?")
    assert question.status_code == 201
    assert reply(client, professor, question.json()["id"], "Yes.").status_code == 201
    assert unread(client, outsider) == 2


def test_subscriber_cannot_start_a_conversation(client, professor, section, outsider):
    follow(client, outsider, "groups", section)
    assert post(client, outsider, recipient_id=professor["id"]).status_code == 403
    assert client.get("/api/v1/messages/contacts", headers=outsider["headers"]).json()["people"] == []


def test_former_subscriber_cannot_reply(client, professor, outsider):
    newsletter = make_newsletter(client, professor)
    follow(client, outsider, "newsletters", newsletter)
    issue = send(client, professor, newsletter_ids=[newsletter])
    unfollow(client, outsider, "newsletters", newsletter)
    assert reply(client, outsider, issue["id"]).status_code == 403


def test_directory_marks_membership_and_subscriptions(client, professor, section, student):
    other_group = create_section(client, professor, "Algorithms")
    newsletter = make_newsletter(client, professor)
    follow(client, student, "groups", other_group)
    body = directory(client, student)
    assert {group["name"]: (group["member"], group["subscribed"]) for group in body["groups"]} == {"Databases": (True, False), "Algorithms": (False, True)}
    assert body["newsletters"][0]["id"] == newsletter
    assert body["newsletters"][0]["owner_name"] == "Test Professor"
    assert body["newsletters"][0]["subscribed"] is False


def test_directory_does_not_expose_rosters(client, section, student, outsider):
    text = client.get("/api/v1/subscriptions", headers=outsider["headers"]).text
    assert student["id"] not in text and "student@example.com" not in text


def test_professors_have_no_subscriptions(client, professor, section):
    assert client.get("/api/v1/subscriptions", headers=professor["headers"]).status_code == 403
    assert follow(client, professor, "groups", section).status_code == 403


def test_unknown_targets_return_404(client, outsider):
    assert follow(client, outsider, "groups", "missing").status_code == 404
    assert follow(client, outsider, "newsletters", "missing").status_code == 404


def test_professor_sees_subscriber_counts_for_own_newsletters_only(client, professor, other_professor, outsider):
    newsletter = make_newsletter(client, professor)
    follow(client, outsider, "newsletters", newsletter)
    [listed] = client.get("/api/v1/newsletters", headers=professor["headers"]).json()
    assert listed["subscriber_count"] == 1
    assert client.get("/api/v1/newsletters", headers=other_professor["headers"]).json() == []
    assert client.get("/api/v1/newsletters", headers=outsider["headers"]).status_code == 403


def test_deleting_a_newsletter_keeps_sent_posts(client, professor, other_professor, outsider):
    newsletter = make_newsletter(client, professor)
    follow(client, outsider, "newsletters", newsletter)
    send(client, professor, newsletter_ids=[newsletter])
    assert client.delete(f"/api/v1/newsletters/{newsletter}", headers=other_professor["headers"]).status_code == 404
    assert client.delete(f"/api/v1/newsletters/{newsletter}", headers=professor["headers"]).status_code == 204
    assert directory(client, outsider)["newsletters"] == []
    assert len(box(client, outsider)) == 1


def test_blank_newsletter_name_is_refused(client, professor):
    assert client.post("/api/v1/newsletters", json={"name": "  "}, headers=professor["headers"]).status_code == 422
