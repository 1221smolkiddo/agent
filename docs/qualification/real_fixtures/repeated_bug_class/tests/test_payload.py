from payload import parse

def test_parse():
    assert parse('1,,2') == [1, 2]
