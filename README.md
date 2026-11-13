# Trip Money & Container Tracking (SPEC-LOG-2026)

Reads scanned container receipts (EIRs), works out each driver's trip money from your rate rules, lets a person
check anything doubtful, and produces the MAMAS trip sheet in Excel. It also keeps the IN/OUT history of every
container.

```
scanned PDF -> page images -> OCR -> read the form fields -> checks -> review queue (you) -> rate rules -> trip log
                                                                                                  |
                                                    Excel trip sheet per driver, summary, CSV, container history
```

Everything runs on one computer, offline. Nothing is sent anywhere.

Verified on Linux with Python 3.12. The Windows commands are the standard equivalents but were not run on Windows.

---

## 1. Install (once)

You need **Python 3.12** and internet for the first install (about 600 MB, mostly the OCR engine and PDF libraries; about 20 seconds on a fast connection). Python 3.13 is not yet supported by the OCR dependency.

**macOS / Linux**

```bash
./install.sh
```

**Windows (PowerShell)**

```powershell
.\install.ps1
```

Both create a virtual environment, install everything, create the database and copy an editable `config/` folder
next to the project. If PowerShell refuses to run scripts: `Set-ExecutionPolicy -Scope Process RemoteSigned`.

<details><summary>Doing it by hand instead</summary>

```bash
python3 -m venv .venv && source .venv/bin/activate        # Windows: py -m venv .venv ; .venv\Scripts\Activate.ps1
pip install -e ".[all]"                                    # everything
tripmoney init
```

Smaller installs: `".[ocr]"` (reading scans), `".[web]"` (web app), `".[dev]"` (tests). The rate engine, database,
exports and tracking need none of them.
</details>

## 2. Daily use

### In the browser

```bash
./start.sh            # Windows: .\start.ps1        (same as: tripmoney serve)
```

Open <http://127.0.0.1:8000>.

1. **Dashboard:** choose the pay period, drop in the scanned PDFs (one PDF per driver) and press *Read scans*.
   Reading takes about 4 seconds a page; the *Uploads* tab shows progress.
2. **Review:** the queue holds only pages that need a person. Each shows the scan beside the fields it read,
   with each warning explained. Fix anything wrong and press *Save & approve*. If a page is not a trip at all (a copy, an
   unpaid receipt) press **Void this page** and give a reason: it is no longer paid, exported or counted, can be
   restored, and the reason is audited. Clean pages are accepted automatically.
3. **Drivers:** the OCR reads names without spaces, so set each driver's proper name, employee number and sponsor
   once. They are kept.
4. **Dashboard / Trips:** download each driver's **Excel trip sheet**, the period **Summary**, or the **CSV**.

| Screen | What it is for |
|---|---|
| Dashboard | Totals per driver for a pay period, uploads, downloads |
| Review | The queue; groups of identical warnings with *Approve all* for the rule decisions; the side-by-side editor; **Void this page** ("not a trip") and **Restore** |
| Trips | Every box and its rate for any driver / period, filterable to rows needing review |
| Containers | Search by container, customer, vessel or EIR number; the "in the depot now" list with days; each box's timeline |
| Add a box by hand | For a box whose scan is missing or unreadable; strictly validated, priced and approved at once |
| Rates | The rate rules in force, which are confirmed and which are spec-only (read-only; edit `config/rates.yaml`) |
| Drivers | Names, employee numbers, sponsors (Iqama numbers are masked) |
| Uploads | Reading progress |
| Audit | Who changed or approved what, and when |

### From the command line

```bash
tripmoney ingest scans/first.pdf scans/second.pdf       # read scans (re-reading the same file is skipped)
tripmoney report --period 2026-10                     # trips and money per driver (2026-10 = 10 Oct to 9 Nov)
tripmoney review                                      # what is waiting for a person
tripmoney approve --flag UNPAIRED_20FT                # accept every page whose only warning is that one
tripmoney void --id 17 --reason "copy of an EIR"      # not a trip: no longer paid or exported (undo: tripmoney restore --id 17)
tripmoney export --period 2026-10 --out exports       # Excel sheets + summary + CSV
tripmoney containers --in-depot                       # boxes in the depot, oldest first
tripmoney containers CRXU8681568                      # one box's history   (or: containers kerry)
tripmoney drivers --set 636 --name "Sample Driver One" --emp-no 4000123 --sponsor SRL   # 636 = last digits shown in the list
tripmoney backup                                      # consistent copy of the database
tripmoney reprice                                     # re-run the rate rules over everything
tripmoney --help
```

Data lives in `./data` (or `$TRIPMONEY_HOME`, or `--home DIR`): the database, page images and the OCR cache.

## 3. The Excel trip sheet

Same layout as the MAMAS sheet (header block, columns, "ok / need check" tie-outs, summary by route, route x customer
table), but self-contained:

- the rates are on a **ROUTE** sheet inside the workbook, with no link to another file;
- *Trip No*, *AMOUNT* and every total are live formulas: change a route code or a rate in Excel and it recalculates;
- two extra columns, **EIR NO** and **CHECK** (`OK` or `REVIEW: <warnings>`), and **To review** in the header, so an
  unchecked row is visible on paper too;
- cells `K3:L4` compare Excel's totals with what the system calculated (`ok` / `differs`), so a hand edit is obvious;
- boxes dated outside the pay period are left out of the totals and listed on an **Outside period** sheet.

Checked by recalculating real exports in LibreOffice: 0 formula errors, cached values equal recalculated values, and
the original sheet's 64 trips / 3,894 reproduce exactly.

## 4. Rates and settings

`tripmoney init` creates an editable **`config/rates.yaml`** (rules are checked top to bottom, first match wins) and
**`config/lexicon.yaml`** (known customer / vessel / shipping-line / remark spellings). Without `config/` the copies
bundled in the package are used.

| Direction (from the EIR) | Size | Status | Route code | Amount | Per |
|---|---|---|---|---|---|
| OUT: depot to port (MLS-JIP) | any | Full | MLS-JIP,OW,FUL | 70 | container |
| IN: port to depot (JIP-MLS) | any | Full | JIP-MLS,OW,FUL | 70 | container |
| MLS-JIP | 20' | Empty | MLS-JIP,OW,MT2 | 57 | **pair** of boxes |
| MLS-JIP | 40' | Empty | MLS-JIP,OW,MT4 | 56 | container |
| JIP-MLS | any | Empty | JIP-MLS,OW,MTY | 59 | container (**unverified**) |

Change a rate by editing the YAML; to change it from a date, add `effective_from` / `effective_to` instead of
overwriting. The same file holds the pay-period start day (10), the 20' pairing window (60 minutes), the currency
label and the container-type abbreviations used in the sheet. Reviewers' corrections to customers and vessels are
remembered automatically, so reading improves with use.

## 5. What happens to a page

1. **Read.** The PDF page is rasterised at 200 dpi and OCR'd (RapidOCR on the CPU). A page scanned upside down or sideways
   is **turned automatically** (tried at 180, 90 and 270 degrees; the orientation that reads the key fields completely wins;
   checked on real scans turned each way). Three layouts are understood:
   the Square Root *Equipment Interchange Receipt*, the DP World container receipt and the Red Sea Gateway Terminal card.
2. **Extract.** Fields are found by their printed labels (not fixed pixel boxes), so a slightly skewed or torn scan still
   reads. Container numbers are fixed with the ISO 6346 check digit (e.g. `00LU` becomes `OOLU`); a number is only
   auto-corrected when exactly one fix works.
3. **Check.** Cross-page checks decide the driver of each page, spot reprints and out-of-period dates (below).
4. **Price.** Direction comes from IN/OUT, then the rate rules and the 20' pairing run.
5. **Review.** Anything with a warning waits for a person; nothing doubtful is silently paid.

| Warning | Meaning |
|---|---|
| `UNPAIRED_20FT` | A 20' empty with no partner box. Priced as a full trip; confirm |
| `RATE_UNVERIFIED` | The rate comes only from the spec |
| `THIRD_PARTY_RECEIPT` | A DP World / Red Sea Gateway receipt: confirm it is a separate paid trip |
| `REPRINT_SUSPECTED` | Printed more than 24 h after the move (a reprint or "Duplicate copy") |
| `OUTSIDE_PERIOD` | Dated outside the pack's pay period; left out of the sheet |
| `DRIVER_MISMATCH` / `TRUCK_MISMATCH` | A hand-corrected or misread page; assigned to the pack's driver and truck |
| `MISSING_CUSTOMER`, `MISSING_*` | A field could not be read (smeared, torn) |
| `CHECK_DIGIT` | The container number fails its check digit |
| `TIME_MISMATCH` | The move time is later than the print time (AM/PM?) |
| `NO_RULE` | No rate rule matches the size / direction / status |

**Accuracy on your 65 supplied pages** (hand-keyed reference, 13 fields per page): **99.8%** of fields read correctly;
all container numbers, dates, sizes, empty/full, directions, EIR numbers, seals and booking numbers are correct. The
two misses are a smeared customer cell and a hand-overwritten truck number, and both are flagged for review. Caveat:
three small fixes were made after seeing the first errors on these same pages, so treat 99.8% as an upper bound for
unseen scans, and expect more review items on worse paper.

## 6. Test it

```bash
pytest                       # everything: expect 224 passed (about 45 seconds)
pytest -v                    # one line per test
pytest tests/test_pairing.py                                       # one file
pytest tests/test_pairing.py::test_two_20ft_empties_are_one_trip   # one test
pytest -k "export or tracking"                                     # by name
python scripts/demo.py       # prices the real trip sheet through the engine + database; prints MATCH
```

| File | Tests | What it proves |
|---|---|---|
| `test_engine_golden.py` | 3 | The engine reproduces all 80 rows / 64 trips / 3,894 of the paid trip sheet |
| `test_rules.py`, `test_pairing.py` | 18 | Every rate rule, effective dates, 20' pairing edge cases |
| `test_normalize.py`, `test_iso6346.py` | 40 | Dates, sizes, plates, container-number repair and check digit |
| `test_layout_lexicon_validate.py` | 20 | Label-anchored reading, name matching, driver resolution, reprint/period checks |
| `test_ocr_regression.py` | 18 | All 65 real scanned pages against the hand-keyed reference (needs no PDFs or OCR engine) |
| `test_pipeline.py` | 22 | Pages to database: drivers, money per driver (26 / 1,572 and 37 / 2,277), flags, duplicates |
| `test_review.py` | 26 | Edits, validation, repricing, approval, bulk approval, void / restore, adding a box by hand, audit |
| `test_orient.py` | 8 | Upside-down and sideways pages are straightened (incl. the case where OCR still recognises the layout) |
| `test_export.py` | 13 | The Excel layout, formulas' totals, pair adjacency, period filter, summary, CSV |
| `test_tracking.py` | 6 | Container history, depot ageing, missing-scan detection, search |
| `test_web.py` | 32 | Every screen, review / void / add flows, downloads, upload end to end, password, cross-site protection (incl. real-browser headers), path safety |
| `test_cli.py`, `test_db_roundtrip.py`, `test_period.py` | 18 | Commands, backup, config, database round trip |

The scan tests use sanitised OCR output stored in `tests/fixtures/` (real layout, dummy driver data), so they run
anywhere. To measure reading on your own PDFs with real OCR:

```bash
tripmoney evaluate --gold tests/fixtures/gold_eir_pages.csv \
    --pack pack1=scans/first.pdf --pack pack2=scans/second.pdf
```

**Check that the tests catch a wrong rate.** Copy `config/rates.yaml` to `bad.yaml`, change `amount: 56` to
`amount: 55`, then `TRIPMONEY_CONFIG=bad.yaml pytest tests/test_engine_golden.py` (PowerShell:
`$env:TRIPMONEY_CONFIG="bad.yaml"`, then `Remove-Item Env:TRIPMONEY_CONFIG` afterwards). Two tests should fail.

Rebuilding fixtures from new data: `python scripts/build_golden_fixture.py sheet.xlsx` (priced trip sheet) and
`python scripts/build_ocr_fixtures.py folder_of_ocr_json` (OCR pages, with driver names and Iqama numbers replaced).

## 7. Data, privacy and security

- Stored locally in SQLite: movements, page images, OCR text, an audit log. Iqama numbers are the driver key; they are
  **masked** (`***636`) everywhere in the web app and CLI, and appear in full only in the Excel sheet, which needs them.
- Nothing leaves the computer: no cloud OCR, no web fonts or scripts (the pages carry a strict content-security policy).
- The app listens on **127.0.0.1** only. To share it on your network you must set a password first:
  `TRIPMONEY_PASSWORD=... tripmoney serve --host 0.0.0.0` (it refuses without one). Browser posts from other sites are refused.
- Every edit, approval, export and bulk action is in the **Audit** tab.
- `tripmoney backup` makes a consistent copy, safe while the app runs (keeps the latest 30). Back up `data/` too if
  you want the page images.

## 8. Decisions for you to confirm

The defaults below are in force, and the review queue makes each one visible.

1. **Spec vs sheet:** where they disagree, the sheet wins (`MT4` is the 40' empty rate; "MLS-TIP-MI2" is `MLS-JIP-MT2`).
2. **A 20' empty with no partner** is priced at 57 and flagged. In your two sample packs this affects **13 boxes**
   (about 740 in total), so it is worth settling, then use *Approve all*.
3. **JIP to MLS empty = 59** comes from the spec only. It affects 4 boxes in the samples. Its route string is assumed.
4. **DP World / Red Sea Gateway receipts** are priced as depot-to-port empty trips and flagged (2 pages in the samples).
5. Pay period 10th to 9th (taken from the linked workbook's name); currency label SAR; `TK` and `FR` container-type
   abbreviations are assumed (`GPD`, `R`, `OT` come from the sheet).

## 9. Known limits

- Pages are straightened when turned by 90/180/270 degrees, not when merely slightly skewed beyond what label-anchored reading tolerates.
- Handwriting is not read; hand-corrected pages are caught by the driver/truck checks and sent to review.
- The "Duplicate copy" watermark is not detected by OCR; the reprint check catches such pages by their print time.
- Badly torn or smeared cells come out blank and are flagged, not guessed.
- A new receipt layout needs a new parser (the three known layouts are handled).
- Single-user, single-machine tool (SQLite). Driver names are unspaced until set once on the Drivers screen.
- Reading speed is about 4 s a page on one CPU core.

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| `tripmoney: command not found` / `No module named tripmoney` | Activate the virtual environment (`source .venv/bin/activate`) and run `pip install -e ".[all]"` |
| `OCR needs the extra packages` / `the web app needs extra packages` | `pip install -e ".[ocr]"` / `".[web]"` |
| Errors about `str | None` or `removeprefix` | Python is older than 3.10 |
| Port already in use | `tripmoney serve --port 8001` |
| A scan page reads badly | Open it in *Review*, correct it, *Save & approve*; the spelling is remembered |
| Want to start clean | Delete the `data/` folder (this removes the database and images) |
| Excel shows `differs` in `L3` / `L4` | Someone changed a route or a rate by hand after export; that is what the check is for |

## 11. Project layout

```
install.sh install.ps1 start.sh start.ps1     one-command setup / start
src/tripmoney/
  defaults/        rates.yaml, lexicon.yaml (bundled copies; `tripmoney init` makes editable ones)
  domain.py rules.py config.py pairing.py engine.py period.py iso6346.py textnorm.py     rate engine
  ocr/             OCR wrapper (RapidOCR) + on-disk cache
  ingest/          pdfio, orient (auto-straighten), layout, extract (3 layouts), normalize, lexicon, validate, pipeline
  db/              SQLAlchemy models, session, repo (store / reprice / summary / review queries)
  review.py        edit / approve / bulk approve / void / restore / add by hand / driver details, all audited
  tracking.py      container history, depot inventory, search
  export/          trip sheet (xlsx), period summary, CSV
  web/             FastAPI app, templates, stylesheet, background upload worker
  evaluate.py cli.py
scripts/           demo.py, build_golden_fixture.py, build_ocr_fixtures.py
tests/             224 tests + fixtures (paid trip sheet, 65 sanitised OCR pages, hand-keyed reference)
```
