import pytest

from enron_pipeline.addresses import clean_address, is_valid_address, parse_address_list, split_items


def test_plain_list():
    r = parse_address_list("ken_rice@enron.com, jeff.skilling@enron.com")
    assert r.valid == ["ken_rice@enron.com", "jeff.skilling@enron.com"]
    assert r.rejected == []


def test_folded_list_after_unfolding():
    # Real folded To header (tab continuation), as returned by headers.unfold().
    value = ("james.bannantine@enron.com, cliff.baxter@enron.com, \tsanjay.bhatnagar@enron.com, "
             "jeremy.blachman@enron.com, \tphilippe.bibi@enron.com")
    assert len(parse_address_list(value).valid) == 5


def test_lotus_leading_dot_is_stripped():
    # skilling-j/deleted_items/9.
    r = parse_address_list("enw <.palmer@enron.com>, legal <.taylor@enron.com>")
    assert r.valid == ["palmer@enron.com", "taylor@enron.com"]


@pytest.mark.parametrize("raw, expected", [
    ("\"'thomas.perkins'\"@enron.com", "thomas.perkins@enron.com"),
    ("claudia\".'\"robinson@enron.com", "claudia.robinson@enron.com"),
    ("\"jhyatt\"@enron.com", "jhyatt@enron.com"),
])
def test_quote_mangled_local_parts_are_repaired(raw, expected):
    assert parse_address_list(raw).valid == [expected]


def test_display_name_with_comma_is_one_address():
    r = parse_address_list('"Lay, Kenneth" <klay@enron.com>, jeff.skilling@enron.com')
    assert r.valid == ["klay@enron.com", "jeff.skilling@enron.com"]
    assert r.rejected == []


def test_invalid_items_are_rejected_without_losing_valid_ones():
    # A malformed item makes strict getaddresses() drop the whole header; we parse per item.
    r = parse_address_list('a@enron.com, <"d@piassick".@enron@enron.com>, b@enron.com')
    assert r.valid == ["a@enron.com", "b@enron.com"]
    assert len(r.rejected) == 1


@pytest.mark.parametrize("value", ["pep <performance.>", '<"d@piassick".@enron@enron.com>',
                                   '"cc:.skerik"@enron.com', "dave."])
def test_unsalvageable_values_give_no_address(value):
    # Real invalid From/To values from lay-k and skilling-j.
    r = parse_address_list(value)
    assert r.valid == []
    assert r.rejected


def test_group_syntax_has_no_addresses_and_no_rejections():
    r = parse_address_list("undisclosed-recipients:;")
    assert r.valid == [] and r.rejected == []


def test_duplicates_removed_case_insensitively_in_order():
    r = parse_address_list("B@Enron.com, a@enron.com, b@enron.com")
    assert r.valid == ["b@enron.com", "a@enron.com"]


def test_non_ascii_local_part_is_kept():
    # kitchen-l/_americas/regulatory/113. (latin-1 'é')
    assert parse_address_list("ren\xe9.lazure@enron.com").valid == ["ren\xe9.lazure@enron.com"]


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_values(value):
    r = parse_address_list(value)
    assert r.valid == [] and r.rejected == []


def test_split_items_respects_quotes_and_brackets():
    assert split_items('"a, b" <x@y.com>, <p,q@r.com>, z@w.com') == ['"a, b" <x@y.com>', "<p,q@r.com>", "z@w.com"]


@pytest.mark.parametrize("addr, ok", [
    ("a@b.com", True), ("u@d.h", True), ("a@b", False), ("@b.com", False),
    ("a@@b.com", False), ("a b@c.com", False), ("a@b.com.", False), ("performance.", False),
])
def test_is_valid_address(addr, ok):
    assert is_valid_address(addr) is ok


def test_clean_address_lowercases_and_strips():
    assert clean_address(" <.Jeff.Skilling@Enron.COM> ") == "jeff.skilling@enron.com"
