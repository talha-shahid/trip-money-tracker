from datetime import datetime

from tripmoney.ingest.layout import Layout
from tripmoney.ingest.lexicon import Lexicon
from tripmoney.ingest.parsed import ParsedPage
from tripmoney.ingest.validate import resolve_pack, validate_pack
from tripmoney.ocr.engine import Box

VOCAB = {"VESSEL": "vessel", "CUSTOMER": "customer", "BOOKINGNO": "booking", "SHIPPINGLINE": "line", "GRADE": "grade"}


def b(text, x0, y0, x1=None, y1=None, conf=0.95):
    return Box(text, x0, y0, x1 or x0 + 10 * len(text), y1 or y0 + 30, conf)


def page(*boxes, w=1653):
    return Layout(list(boxes), VOCAB, w)


class TestLayout:
    def test_reads_the_value_under_a_label(self):
        lay = page(b("VESSEL", 70, 100), b("KR TASMAN", 70, 140), b("CUSTOMER", 70, 300))
        assert lay.read("vessel") == ("KR TASMAN", 0.95)

    def test_stops_at_the_next_label_below_and_the_next_label_on_the_row(self):
        lay = page(b("CUSTOMER", 70, 100), b("BOOKINGNO.", 600, 100), b("ACME", 70, 140), b("TFS123", 600, 140),
                   b("SHIPPINGLINE", 70, 260), b("ZZZ", 70, 300))
        assert lay.read("customer")[0] == "ACME" and lay.read("booking")[0] == "TFS123"

    def test_wrapped_value_reads_top_line_first(self):
        lay = page(b("BOOKINGNO.", 600, 100), b("MSCSIENARSGT1410", 600, 140), b("26", 596, 175))
        assert lay.read("booking", join="")[0] == "MSCSIENARSGT141026"

    def test_cap_limits_how_far_below_a_label_to_look(self):
        lay = page(b("GRADE", 70, 100), b("P", 70, 140), b("stray", 70, 400))
        assert lay.read("grade", cap=120)[0] == "P"

    def test_missing_label_gives_empty(self):
        assert page(b("hello", 0, 0)).read("vessel") == ("", 0.0)

    def test_ocr_typo_in_a_label_still_matches(self):
        lay = page(b("CUST0MER", 70, 100), b("ACME", 70, 140))
        assert lay.read("customer")[0] == "ACME"

    def test_low_confidence_noise_can_be_dropped(self):
        lay = page(b("VESSEL", 70, 100), b("REAL", 70, 140), b("smudge", 200, 140, conf=0.4))
        assert lay.read("vessel", min_conf=0.85)[0] == "REAL"

    def test_scales_with_page_width(self):
        small = Layout([b("VESSEL", 35, 50, 90, 65), b("KR", 35, 70, 60, 85)], VOCAB, 826)
        assert small.read("vessel")[0] == "KR"


class TestLexicon:
    lex = Lexicon({"customer": ["TRIDENT FREIGHT SAUDI L L L C", "Maersk"], "vessel": ["ZHONG GU NAN JING"]})

    def test_restores_spaces_ignoring_case_and_punctuation(self):
        assert self.lex.canonical("customer", "TRIDENTFREIGHTSAUDILLLC") == "TRIDENT FREIGHT SAUDI L L L C"
        assert self.lex.canonical("customer", "maersk.") == "Maersk"

    def test_tolerates_one_misread_character(self):
        assert self.lex.canonical("vessel", "ZH0NGGUNANJING") == "ZHONG GU NAN JING"

    def test_unknown_names_are_kept_not_invented(self):
        assert self.lex.canonical("customer", "Brand New Co.") == "Brand New Co"
        assert self.lex.canonical("customer", "") == ""

    def test_adding_a_name_teaches_it(self):
        lex = Lexicon()
        lex.add("customer", "Salem Al-Saiary Est")
        assert lex.canonical("customer", "SALEMALSAIARYEST") == "Salem Al-Saiary Est"


def sr(iqama, truck="RJA3518", name="D", when=datetime(2026, 10, 20, 10), printed=None, truck_raw=None):
    return ParsedPage("sr_eir", iqama=iqama, truck=truck, truck_raw=truck_raw or truck, driver_name=name,
                      occurred_at=when, printed_at=printed)


def card(truck):
    return ParsedPage("dpworld", truck=truck, truck_raw=truck)


class TestResolvePack:
    def test_main_driver_and_name_by_majority(self):
        pages = [sr("1111111111", name="DRIVERA") for _ in range(4)] + [sr("1111111111", name="DRIVERB")]
        out = resolve_pack(pages)
        assert {r.driver_key for r in out} == {"1111111111"} and {r.driver_name for r in out} == {"DRIVERA"}

    def test_stray_iqama_goes_to_the_main_driver_and_is_flagged(self):
        pages = [sr("1111111111") for _ in range(4)] + [sr("2222222222", truck="NJA5377")]
        out = resolve_pack(pages)
        assert out[-1].driver_key == "1111111111" and out[-1].truck == "RJA3518"
        assert {"DRIVER_MISMATCH", "TRUCK_MISMATCH"} <= {f.code for f in pages[-1].flags}

    def test_terminal_card_is_matched_by_truck(self):
        pages = [sr("1111111111", truck="RJA3518") for _ in range(3)] + [sr("2222222222", truck="NSA6128") for _ in range(3)]
        pages.append(card("NSA6128"))
        assert resolve_pack(pages)[-1].driver_key == "2222222222"

    def test_a_pack_with_two_real_drivers_is_split(self):
        pages = [sr("1111111111") for _ in range(3)] + [sr("2222222222", truck="NSA6128") for _ in range(3)]
        assert [r.driver_key for r in resolve_pack(pages)] == ["1111111111"] * 3 + ["2222222222"] * 3

    def test_no_iqama_at_all_is_an_error_not_a_guess(self):
        pages = [sr("")]
        assert resolve_pack(pages)[0].driver_key == "UNKNOWN" and "NO_DRIVER" in {f.code for f in pages[0].flags}

    def test_forced_driver(self):
        assert {r.driver_key for r in resolve_pack([sr("1111111111"), sr("2222222222")], forced_key="X")} == {"X"}


class TestValidatePack:
    def test_outlier_date_and_reprint(self):
        pages = [sr("1", when=datetime(2026, 10, d)) for d in (12, 15, 18, 20)]
        pages.append(sr("1", when=datetime(2026, 9, 21)))
        pages.append(sr("1", when=datetime(2026, 10, 14, 21), printed=datetime(2026, 10, 18, 4)))
        validate_pack(pages)
        codes = [{f.code for f in p.flags} for p in pages]
        assert "OUTSIDE_PERIOD" in codes[4] and "REPRINT_SUSPECTED" in codes[5]
        assert not codes[0] and not codes[1]

    def test_print_time_before_the_move_flags_am_pm_problem(self):
        p = sr("1", when=datetime(2026, 10, 21, 0, 33), printed=datetime(2026, 10, 20, 12, 37))
        validate_pack([p])
        assert "TIME_MISMATCH" in {f.code for f in p.flags}
