import pytest

from helpers import load_golden
from tripmoney.iso6346 import check_digit, is_valid, split_container_no


def test_known_valid_numbers_from_the_scans():
    for n in ("CRXU8681568", "OOLU9230830", "ZGLU2093344", "MRKU0318280"):
        assert is_valid(n), n


def test_trailing_letter_is_kept_out_of_validation():
    assert split_container_no("ZGXU3098707A") == ("ZGXU3098707", "A")
    assert is_valid("ZGXU3098707A") and is_valid("CICU6920147A")


@pytest.mark.parametrize("bad", ["CRXU8681569", "CRXU868156", "1234ABCDEFG", "", "CRXU86815O8"])
def test_invalid_numbers_are_rejected(bad):
    assert not is_valid(bad)


def test_single_digit_change_is_always_caught():
    base = "OOLU923083"  # first 10 chars of a valid number
    assert check_digit(base) == 0
    assert not is_valid(base + "5")


def test_every_container_in_the_trip_sheet_passes_the_check_digit():
    rows, _ = load_golden()
    bad = [r["container_no"] for r in rows if not is_valid(r["container_no"])]
    assert bad == []
