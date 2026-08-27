from org_harvest.errors import ErrorKind, OrgHarvestError


def test_carries_kind_and_message():
    err = OrgHarvestError("boom", kind=ErrorKind.AUTH_FAILED)
    assert str(err) == "boom"
    assert err.kind is ErrorKind.AUTH_FAILED


def test_is_a_plain_exception():
    assert issubclass(OrgHarvestError, Exception)
