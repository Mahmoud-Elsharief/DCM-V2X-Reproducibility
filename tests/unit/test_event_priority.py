from event import Event


def _noop():
    pass


def test_mobility_priority_precedes_radio_event_at_same_time():
    mobility = Event(0.1, _noop, priority=-100)
    radio = Event(0.1, _noop, priority=0)
    assert mobility < radio
