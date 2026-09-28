from payload import ordered

def test_ordered():
    assert ordered(['b', 'a']) == ['a', 'b']
