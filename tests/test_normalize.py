from datetime import datetime

import pytest

from tripmoney.ingest import normalize as nz


@pytest.mark.parametrize("raw, expected", [
    ("October25,20264:37AM", datetime(2026, 10, 25, 4, 37)),
    ("October 22, 2026 11:39 PM", datetime(2026, 10, 22, 23, 39)),
    ("September21,20263:04PM", datetime(2026, 9, 21, 15, 4)),
    ("October21,202612:33AM", datetime(2026, 10, 21, 0, 33)),   # 12 AM is just after midnight
    ("October14,202612:13PM", datetime(2026, 10, 14, 12, 13)),  # 12 PM is noon
    ("0ctober19,20268:44PM", datetime(2026, 10, 19, 20, 44)),   # O read as zero
])
def test_eir_datetime(raw, expected):
    assert nz.parse_eir_datetime(raw) == expected


@pytest.mark.parametrize("raw", ["", "N/A", "Octoberxx,2026", "October32,20264:37AM", "October25,202613:37AM"])
def test_eir_datetime_rejects_garbage(raw):
    assert nz.parse_eir_datetime(raw) is None


def test_other_date_formats():
    assert nz.parse_printed("19-0ct-2620:48") == datetime(2026, 10, 19, 20, 48)
    assert nz.parse_dmy_datetime("21-10-202621:03") == datetime(2026, 10, 21, 21, 3)
    assert nz.parse_iso_datetime("2026-10-1816:38:31") == datetime(2026, 10, 18, 16, 38)


@pytest.mark.parametrize("raw, size", [("20ft", 20), ("40ft", 40), ("4Oft", 40), ("45ft", 45), ("", None), ("ft", None)])
def test_size(raw, size):
    assert nz.size_from_text(raw) == size


def test_iso_type_codes():
    assert (nz.size_from_iso("42G1"), nz.ctype_from_iso("42G1")) == (40, "GP")
    assert (nz.size_from_iso("22G1"), nz.ctype_from_iso("22G1")) == (20, "GP")
    assert nz.ctype_from_iso("45R1") == "REEFER"


def test_status_and_type():
    assert [nz.status_from_text(x) for x in ("Empty", "FULL", "Fuli", "Emptv", "", "xyz")] == \
        ["EMPTY", "FULL", "FULL", "EMPTY", None, None]
    assert [nz.ctype_from_text(x) for x in ("GeneralPurpose", "Tank", "Reefer", "OpenTop", "")] == \
        ["GP", "TANK", "REEFER", "OPEN_TOP", None]


def test_plate_has_one_canonical_form():
    assert {nz.normalize_plate(x) for x in ("RJA-3518", "3518RJA", "RJA 3518", "rja3518")} == {"RJA3518"}
    assert nz.normalize_plate("6128NSA") == nz.normalize_plate("NSA-6128") == "NSA6128"


def test_iqama_needs_exactly_ten_digits():
    assert nz.iqama_digits("1234567890") == "1234567890"
    assert nz.iqama_digits("254325663") == "" and nz.iqama_digits("") == ""


def test_not_available_variants_become_blank():
    assert [nz.clean_party(x) for x in ("N/A", "V/N", "NA", "Kerry.", " COSCO. ")] == ["", "", "", "Kerry", "COSCO"]


def test_reference_numbers():
    assert nz.reference_no("BARQEXP0CT2026") == "BARQEXPOCT2026"      # 0 between letters is an O
    assert nz.reference_no("HLK0576815") == "HLK0576815"              # leading zero of a number stays
    assert nz.reference_no("EP72599020") == "EP72599020"
    assert nz.reference_no("N/A") == ""


class TestContainerRepair:
    def test_valid_number_is_untouched(self):
        assert nz.repair_container("CRXU8681568") == ("CRXU8681568", "", False, True)

    def test_trailing_letter_is_kept_as_suffix(self):
        assert nz.repair_container("ZGXU3098707A") == ("ZGXU3098707", "A", False, True)

    def test_zero_for_o_in_owner_code_is_repaired(self):
        core, _, repaired, valid = nz.repair_container("00LU9230830")
        assert (core, repaired, valid) == ("OOLU9230830", True, True)

    def test_letter_for_digit_is_repaired(self):
        core, _, repaired, valid = nz.repair_container("CRXU868I568")
        assert (core, repaired, valid) == ("CRXU8681568", True, True)

    def test_a_wrong_digit_is_not_guessed(self):
        core, _, _, valid = nz.repair_container("CRXU8681569")  # check digit fails; no OCR-style fix exists
        assert not valid

    def test_short_input_is_invalid(self):
        assert nz.repair_container("CRXU86")[3] is False

    def test_typed_numbers_are_never_guessed(self):
        core, _, _, valid = nz.repair_container("TGCU0162378", guess=False)
        assert (core, valid) == ("TGCU0162378", False)
