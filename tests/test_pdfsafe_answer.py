"""The answer a PDF-reading child process sends back is untrusted (the child parses the hostile file), so it is read with an allow-listed unpickler."""

import datetime
import os
import pickle

import pytest

from app import pdfsafe


class _Evil:
    def __reduce__(self):
        return (os.system, ("echo pwned",))


def test_an_answer_that_would_run_code_is_refused():
    with pytest.raises(pickle.UnpicklingError):
        pdfsafe._read_answer(pickle.dumps(("ok", _Evil())))


def test_an_answer_naming_any_other_function_or_class_is_refused():
    for blob in (pickle.dumps(("ok", os.getcwd)), pickle.dumps(("ok", subprocess_popen())), pickle.dumps(("ok", __import__))):
        with pytest.raises(pickle.UnpicklingError):
            pdfsafe._read_answer(blob)


def subprocess_popen():
    import subprocess
    return subprocess.Popen


def test_plain_data_dates_exceptions_and_the_result_dataclasses_come_through():
    from app.library.price_lists.reader import PriceListData
    data = PriceListData(rows=[], shipping_note="a", warehouse_hint=None, unread_spec_lines=[])
    kind, value = pdfsafe._read_answer(pickle.dumps(("ok", {"text": "x", "n": 3, "when": datetime.date(2026, 1, 2), "data": data, "pairs": [(1, 2.5)]})))
    assert kind == "ok" and value["when"] == datetime.date(2026, 1, 2) and value["data"] == data and value["pairs"] == [(1, 2.5)]
    kind, exc = pdfsafe._read_answer(pickle.dumps(("err", ValueError("bad"))))
    assert isinstance(exc, ValueError) and str(exc) == "bad"
    kind, exc = pdfsafe._read_answer(pickle.dumps(("err", pdfsafe.PdfUnreadable("slow"))))
    assert isinstance(exc, pdfsafe.PdfUnreadable)


def test_a_child_that_answers_with_a_hostile_pickle_makes_the_pdf_unreadable_not_the_server_compromised(monkeypatch):
    class Done:
        stdout = pickle.dumps(("ok", _Evil()))
    monkeypatch.setattr(pdfsafe.subprocess, "run", lambda *a, **k: Done)
    with pytest.raises(pdfsafe.PdfUnreadable):
        pdfsafe.run("app.labs.labcorp:_page_count_here", b"%PDF")
