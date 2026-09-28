from payload import migrate

def test_migrate():
    assert migrate({'legacy_version': 1}) == {'version': 1}
    assert migrate({'version': 2}) == {'version': 2}
