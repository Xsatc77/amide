"""Reading a PDF that came from outside, in a child process that can be stopped.

pdfplumber (pdfminer) has no limit on how far a stream inflates and no way to be interrupted: a crafted PDF can use gigabytes
of memory or spin for hours inside a worker thread that cannot be killed. `run` starts the reader in its own Python process, gives it
a time limit (and, where the system allows, a memory limit) and kills it when either runs out. The child is the same code a thread would
have run, so a file that reads in-process reads here too; only the way it fails is different.

    result = pdfsafe.run("app.labs.reports:extract_text_here", data)        # "module:function", arguments and result are pickled

Arguments and result must be picklable; the function must be importable at the top level of its module."""

import dataclasses
import enum
import importlib
import io
import os
import pickle
import subprocess
import sys

TIMEOUT = 45             # seconds a PDF may take to read
MEMORY_MB = 1536         # address space the child may use (Linux and macOS; the time limit is the only guard where the system has no such limit)


# The child is the process that parses the hostile file, so ITS ANSWER IS UNTRUSTED: if a bug in a PDF library let a crafted file take the
# child over, a plain pickle.loads here would hand it code execution in the web server. The answer is read with an unpickler that only
# builds plain data (numbers, text, lists, dates), the result dataclasses of the readers, the exceptions they raise, and nothing else.
_PLAIN_NAMES = {"datetime": {"date", "datetime", "time", "timedelta", "timezone"}, "decimal": {"Decimal"}, "collections": {"OrderedDict"}}
_RESULT_PACKAGES = ("app.library.price_lists.", "app.labs.")


class _AnswerUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if module == "builtins":
            obj = getattr(__import__("builtins"), name, None)
            if isinstance(obj, type) and (issubclass(obj, Exception) or obj in (set, frozenset, dict, list, tuple, str, bytes, int, float, bool, complex, bytearray, slice, range)):
                return obj
        elif name in _PLAIN_NAMES.get(module, ()):
            return getattr(importlib.import_module(module), name)
        elif module == "app.pdfsafe" and name == "PdfUnreadable":
            return PdfUnreadable
        elif module.startswith(_RESULT_PACKAGES):
            obj = getattr(importlib.import_module(module), name, None)
            if isinstance(obj, type) and (dataclasses.is_dataclass(obj) or issubclass(obj, (enum.Enum, ValueError))):
                return obj
        raise pickle.UnpicklingError(f"{module}.{name} is not allowed in a reader's answer")


def _read_answer(blob: bytes):
    return _AnswerUnpickler(io.BytesIO(blob)).load()


class PdfUnreadable(ValueError):
    """The reader stopped (too slow, too large, or it died) or raised something that could not be passed on; the message is safe to show."""


def run(target: str, *args, timeout: float = TIMEOUT, memory_mb: int = MEMORY_MB):
    """Call `target` ("module:function") with `args` in a child process and return its result. An exception the function raises is raised
    here as the same exception when it can be passed on; a timeout, a crash or a memory overrun raises PdfUnreadable."""
    try:
        payload = pickle.dumps((target, args))
    except Exception:
        raise PdfUnreadable("the PDF could not be passed to the reader") from None
    kwargs = {}
    if os.name == "posix" and memory_mb > 0:          # 0 = no address-space cap (the OCR engine reserves a lot of address space it never uses; the time limit still applies)
        def limit():                                                  # runs in the child before it starts
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (memory_mb << 20, memory_mb << 20))
        kwargs["preexec_fn"] = limit
    try:
        done = subprocess.run([sys.executable, "-m", "app.pdfsafe"], input=payload, capture_output=True, timeout=timeout,
                              cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), **kwargs)
    except subprocess.TimeoutExpired:
        raise PdfUnreadable("the PDF took too long to read") from None
    except OSError:
        raise PdfUnreadable("the PDF could not be read") from None
    try:
        kind, value = _read_answer(done.stdout)
    except Exception:                                                 # no answer: the child was killed (memory) or crashed
        raise PdfUnreadable("the PDF could not be read (it needs too much memory or is damaged)") from None
    if kind == "ok":
        return value
    if isinstance(value, BaseException):
        raise value
    raise PdfUnreadable(str(value))


def _child() -> None:                                                 # pragma: no cover  (runs in the child process)
    out = sys.stdout.buffer
    sys.stdout = sys.stderr                                           # a library that prints must not corrupt the answer
    target, args = pickle.loads(sys.stdin.buffer.read())
    module, _, name = target.partition(":")
    try:
        answer = ("ok", getattr(importlib.import_module(module), name)(*args))
    except MemoryError:
        answer = ("err", "the PDF needs too much memory")
    except BaseException as exc:                                      # noqa: BLE001 -- everything goes back to the caller
        answer = ("err", exc)
    try:
        blob = pickle.dumps(answer)
    except Exception:
        blob = pickle.dumps(("err", f"{type(answer[1]).__name__}"))
    out.write(blob)
    out.flush()


if __name__ == "__main__":                                            # pragma: no cover
    _child()
