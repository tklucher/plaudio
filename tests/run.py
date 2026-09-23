"""Minimal runner so tests work with or without pytest: python tests/run.py"""
import pathlib, sys, tempfile, traceback
here = pathlib.Path(__file__).parent
sys.path[:0] = [str(here.parent), str(here)]
import test_plaudio as t
fails = 0
for n in sorted(dir(t)):
    if n.startswith("test_"):
        f = getattr(t, n)
        try:
            f(pathlib.Path(tempfile.mkdtemp())) if f.__code__.co_argcount else f()
            print("PASS", n)
        except Exception:
            fails += 1; print("FAIL", n); traceback.print_exc()
sys.exit(fails)
