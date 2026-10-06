from watcher.amide_client import Source
from watcher.ports import Group, Topic
from watcher.tests.fakes import FakeAmide, FakeTelegram
from watcher.tests.test_runner import build, msg, poll


def tmsg(chat, mid, text, topic, minutes_ago=30):
    m = msg(chat, mid, text, minutes_ago)
    m.topic_id = topic
    return m


def forum(tmp_path, watch, messages):
    tg = FakeTelegram(groups=[Group("-100", "Acme group", [Topic("7", "US warehouse"), Topic("8", "Chatter")])], messages={"-100": messages})
    return build(tmp_path, tg=tg, amide=FakeAmide(watch=watch))


def test_topics_are_registered_with_the_group_once_and_again_when_they_change(tmp_path):
    runner, tg, amide, *_ = forum(tmp_path, [], [])
    poll(runner)
    poll(runner)
    assert amide.registered == [("-100", "Acme group", [("7", "US warehouse"), ("8", "Chatter")])]
    tg.groups[0].topics.append(Topic("9", "UK"))
    poll(runner)
    assert len(amide.registered) == 2 and ("9", "UK") in amide.registered[1][2]


def test_only_the_selected_topics_are_read_and_each_message_carries_its_topic(tmp_path):
    runner, tg, amide, state, *_ = forum(tmp_path, [Source("-100", ["7"])], [tmsg("-100", 1, "list here", "7"), tmsg("-100", 2, "chat", "8")])
    poll(runner)
    assert [(p.message_id, p.topic_id, p.topic_title) for p in amide.sent] == [("1", "7", "US warehouse")]
    assert not [c for c in tg.calls if c[:3] == ("messages_since", "-100", "8")]                  # the chatter topic is never fetched


def test_each_topic_has_its_own_position(tmp_path):
    runner, tg, amide, state, *_ = forum(tmp_path, [Source("-100", ["7", "8"])],
                                         [tmsg("-100", 10, "a", "7"), tmsg("-100", 3, "b", "8")])
    poll(runner)
    assert state.chat("-100#7").last_id == 10 and state.chat("-100#8").last_id == 3
    tg.messages["-100"].append(tmsg("-100", 11, "c", "8"))
    poll(runner)
    assert [p.message_id for p in amide.sent] == ["10", "3", "11"]                                 # 3 was not skipped by 10


def test_a_group_followed_whole_is_read_without_a_topic_and_an_empty_selection_reads_nothing(tmp_path):
    runner, tg, amide, *_ = forum(tmp_path, [Source("-100", None)], [tmsg("-100", 1, "x", "7")])
    poll(runner)
    assert [(p.message_id, p.topic_id) for p in amide.sent] == [("1", "7")]                        # a whole-group read keeps each message's topic
    runner2, tg2, amide2, *_ = forum(tmp_path / "b", [Source("-100", [])], [tmsg("-100", 1, "x", "7")])
    poll(runner2)
    assert amide2.sent == [] and not [c for c in tg2.calls if c[0] == "messages_since"]


def test_the_general_topic_and_a_group_that_is_not_a_forum_work(tmp_path):
    runner, tg, amide, *_ = forum(tmp_path, [Source("-100", ["1"])], [tmsg("-100", 5, "general list", "1")])
    poll(runner)
    assert [p.topic_id for p in amide.sent] == ["1"]
    plain = FakeTelegram(groups=[Group("-200", "Plain group")], messages={"-200": [msg("-200", 1, "hello")]})
    runner2, tg2, amide2, *_ = build(tmp_path / "p", tg=plain, amide=FakeAmide(watch=["-200"]))
    poll(runner2)
    assert [(p.topic_id, p.topic_title) for p in amide2.sent] == [(None, None)]
