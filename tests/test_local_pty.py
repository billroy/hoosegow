import base64
import time
from types import SimpleNamespace

from server.local_pty import LocalPtyDriver, LocalPtySession


def _session():
    return LocalPtySession(
        id="test",
        process=SimpleNamespace(pid=1234),
        fd=-1,
        cwd="/tmp",
        shell="/bin/zsh",
        created_at=time.time(),
    )


def test_local_pty_records_terminal_bytes_without_interpreting_them():
    driver = LocalPtyDriver()
    session = _session()
    driver.sessions[session.id] = session
    raw = (
        b"%                                                                              \r \r\r"
        b"bill@Blackbird hoosegow % "
    )

    driver._append_event(
        session,
        {
            "event": "output",
            "data": base64.b64encode(raw).decode("ascii"),
        },
    )

    polled = driver.poll(session.id, since=0, timeout=0)
    event = polled["events"][0]

    assert event["event"] == "output"
    assert base64.b64decode(event["data"]) == raw


def test_local_pty_event_history_is_bounded():
    driver = LocalPtyDriver(history_limit=128)
    session = _session()
    driver.sessions[session.id] = session

    for index in range(129):
        driver._append_event(
            session,
            {
                "event": "output",
                "data": base64.b64encode(str(index).encode("ascii")).decode("ascii"),
            },
        )

    polled = driver.poll(session.id, since=0, timeout=0)
    payloads = [base64.b64decode(event["data"]) for event in polled["events"]]

    assert payloads[0] == b"1"
    assert payloads[-1] == b"128"
    assert len(payloads) == 128
    assert polled["next_seq"] == 129
