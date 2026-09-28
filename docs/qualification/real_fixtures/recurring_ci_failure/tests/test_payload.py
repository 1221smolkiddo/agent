from payload import load

def test_load(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load() == 'configured'
