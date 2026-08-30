from kvstore.store import KVStore


def test_put_get():
    s = KVStore()
    rev = s.put("a", "1")
    assert s.get("a") == "1"
    assert rev == 1


def test_cas():
    s = KVStore()
    s.put("a", "1")
    assert s.cas("a", 1, "2")
    assert not s.cas("a", 1, "3")
    assert s.get("a") == "2"


def test_delete():
    s = KVStore()
    s.put("a", "1")
    assert s.delete("a")
    assert s.get("a") is None
