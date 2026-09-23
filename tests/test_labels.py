"""The implementation-effect analyser that produces gold labels."""

from data.labels.ast_effects import class_method_effects, tau1_invoke_effects


def invoke(body: str) -> bool:
    src = "def invoke(data, x):\n" + "\n".join("    " + line for line in body.strip().splitlines())
    return tau1_invoke_effects(src).mutates


def test_direct_and_aliased_writes_count():
    assert invoke('data["users"][x] = 1')
    assert invoke('users = data["users"]\nuser = users[x]\nuser["name"] = "a"')
    assert invoke('reservations, users = data["reservations"], data["users"]\nreservations[x] = {}')
    assert invoke('for r in data["rs"]:\n    r["status"] = "cancelled"')
    assert invoke('data["log"].append(x)')


def test_reads_and_fresh_containers_do_not():
    assert not invoke('return data["users"][x]')
    assert not invoke('out = {"a": data["a"]}\nout["b"] = 1\nreturn out')
    assert not invoke('rows = [r for r in data["rs"]]\nrows.append(1)\nreturn rows')
    assert not invoke('from copy import deepcopy\nc = deepcopy(data["a"])\nc["k"] = 1')
    assert not invoke('flights = deepcopy(x)\nfor f in flights:\n    f["price"] = 1')


def test_element_of_fresh_container_is_still_environment():
    assert invoke('rows = [r for r in data["rs"]]\nrows[0]["status"] = "x"')


CLASSES = '''
class Directory:
    def __init__(self):
        self.contents = {}
    def _add_file(self, name):
        self.contents[name] = ""

class FS:
    def __init__(self):
        self._cwd = Directory()
        self._cache = {}
    def touch(self, name):
        self._cwd._add_file(name)
    def ls(self):
        return list(self._cwd.contents)
    def reset(self):
        self._clear()
    def _clear(self):
        self._cache.clear()
'''


def test_mutation_through_another_class_and_through_helpers():
    fx = class_method_effects(CLASSES)
    assert fx["touch"].mutates                 # via Directory._add_file
    assert not fx["ls"].mutates
    assert fx["reset"].mutates and "self._clear()" in fx["reset"].evidence[-1]
    assert "_clear" not in fx and "__init__" not in fx   # private methods are not tools
