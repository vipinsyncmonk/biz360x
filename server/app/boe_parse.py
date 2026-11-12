"""Read a Bill of Entry as customs prints it (the ICEGATE "assessed copy" PDF) into the fields the import record needs.

The PDF is a form: labels in a header row, values in the row under it, and blank cells simply absent. So a value is matched to
the label whose column it sits under, never by counting values. Nothing here posts or saves; the caller shows the result to a
person to check first."""
from __future__ import annotations

import datetime as dt
import io
import re
from decimal import Decimal, InvalidOperation

MONTHS = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


# ISO codes for the countries goods are usually imported from, by the name customs prints. Anything else is left for the person to set.
COUNTRIES = {"JAPAN": "JP", "CHINA": "CN", "UNITED STATES": "US", "USA": "US", "U S A": "US", "GERMANY": "DE", "UNITED KINGDOM": "GB", "UK": "GB", "SINGAPORE": "SG", "HONG KONG": "HK",
             "TAIWAN": "TW", "KOREA, REPUBLIC OF": "KR", "SOUTH KOREA": "KR", "UNITED ARAB EMIRATES": "AE", "UAE": "AE", "FRANCE": "FR", "ITALY": "IT", "MALAYSIA": "MY", "THAILAND": "TH",
             "VIETNAM": "VN", "NETHERLANDS": "NL", "SWITZERLAND": "CH", "FINLAND": "FI", "SWEDEN": "SE", "ISRAEL": "IL", "CANADA": "CA", "AUSTRALIA": "AU", "INDONESIA": "ID"}


class NotABillOfEntry(Exception):
    pass


def read_pdf_text(data: bytes) -> list[str]:
    """The text of each page, keeping the columns. PDFs without a text layer (scans) give nothing and are refused by the caller."""
    import logging
    from pypdf import PdfReader
    logging.getLogger("pypdf").setLevel(logging.ERROR)                 # it warns about the rotated side labels, which carry nothing we read
    try:
        reader = PdfReader(io.BytesIO(data))
        return [(p.extract_text(extraction_mode="layout") or "") for p in reader.pages]
    except Exception as e:                                            # a damaged or encrypted file
        raise NotABillOfEntry("That file could not be read as a PDF") from e


def _num(s: str | None) -> Decimal | None:
    if s is None:
        return None
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        return None


def _date(s: str | None) -> dt.date | None:
    if not s:
        return None
    s = s.strip()
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        try:
            return dt.date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{2})-([A-Za-z]{3})-(\d{2,4})", s)
    if m and m[2].upper() in MONTHS:
        y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        try:
            return dt.date(y, MONTHS[m[2].upper()], int(m[1]))
        except ValueError:
            return None
    return None


def _header_row(lines: list[str], *needles: str) -> int | None:
    for i, ln in enumerate(lines):
        if all(n in ln for n in needles):
            return i
    return None


def _cells(header: str, labels: dict[str, str], row: str) -> dict[str, str]:
    """Match the numbers of `row` to the labels of `header` they sit under. Blank cells have no number, so the match keeps the left-to-right
    order and picks, of all the ways to do that, the one that leaves the numbers nearest their labels (the print is not exactly aligned)."""
    spans: list[tuple[float, str]] = []
    for key, text in labels.items():
        i = header.find(text)
        if i >= 0:
            spans.append((i + len(text) / 2, key))
    spans.sort()
    toks = [((m.start() + m.end()) / 2, m.group()) for m in re.finditer(r"\S+", row) if re.fullmatch(r"[0-9][0-9,]*(?:\.[0-9]+)?", m.group())]
    n, k = len(toks), len(spans)
    if n == 0 or n > k:
        return {}
    INF = float("inf")
    best = [[INF] * (k + 1) for _ in range(n + 1)]                    # best[i][j]: cost of placing the first i numbers among the first j labels
    take = [[False] * (k + 1) for _ in range(n + 1)]
    best[0] = [0.0] * (k + 1)
    for i in range(1, n + 1):
        for j in range(i, k + 1):
            skip = best[i][j - 1]
            use = best[i - 1][j - 1] + abs(toks[i - 1][0] - spans[j - 1][0])
            if use <= skip:
                best[i][j], take[i][j] = use, True
            else:
                best[i][j] = skip
    out: dict[str, str] = {}
    i, j = n, k
    while i > 0 and j > 0:
        if take[i][j]:
            out[spans[j - 1][1]] = toks[i - 1][1]
            i -= 1
        j -= 1
    return out


def parse_boe(pages: list[str]) -> dict:
    """The fields of a bill of entry, or NotABillOfEntry. Missing figures are None, never guessed."""
    text = "\n".join(pages)
    flat = re.sub(r"\s+", " ", text.upper())
    if "BILL OF ENTRY" not in flat or "INDIAN CUSTOMS" not in flat:
        raise NotABillOfEntry("This does not look like a customs Bill of Entry")
    lines = text.splitlines()
    warnings: list[str] = []

    head = re.search(r"Port Code\s+BE No\s+BE Date.*\n\s*([A-Z]{2}[A-Z0-9]{4})\s+(\d{3,12})\s+(\d{2}/\d{2}/\d{4})", text)
    if not head:
        raise NotABillOfEntry("The port, number and date of the bill of entry were not found")
    iec = re.search(r"IEC/Br\s+([A-Z0-9]{10})", text)
    gstin = re.search(r"GSTIN/TYPE\s+([0-9A-Z]{15})", text)
    importer = None
    i = _header_row(lines, "1.IMPORTER")
    if i is not None and i + 1 < len(lines):
        importer = re.split(r"\s{3,}", lines[i + 1].strip())[0] or None

    # duty summary: two rows of labels, each with its values underneath
    duty: dict[str, Decimal] = {}
    i = _header_row(lines, "1.BCD", "2.ACD", "3.SWS")
    if i is not None and i + 1 < len(lines):
        cells = _cells(lines[i], {"bcd": "1.BCD", "acd": "2.ACD", "sws": "3.SWS", "nccd": "4.NCCD", "add": "5.ADD", "cvd": "6.CVD", "igst": "7.IGST", "cess": "8.G.CESS", "assessable": "18.TOT.ASS VAL"}, lines[i + 1])
        duty.update({k: _num(v) for k, v in cells.items() if _num(v) is not None})
    i = _header_row(lines, "9.SG", "14.TOTAL DUTY")
    if i is not None:
        for off in (1, 2):                                            # the values can sit one row lower when a side label intervenes
            if i + off >= len(lines):
                break
            cells = _cells(lines[i], {"sg": "9.SG", "saed": "10.SAED", "gsia": "11.GSIA", "tta": "12.TTA", "health": "13.HEALTH", "total": "14.TOTAL DUTY", "interest": "15.INT", "penalty": "16.PNLTY", "fine": "17.FINE", "amount": "19.TOT. AMOUNT"}, lines[i + off])
            got = {k: _num(v) for k, v in cells.items() if _num(v) is not None}
            if got:
                duty.update(got)
                break

    rate = re.search(r"1\s+([A-Z]{3})\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*INR", text)
    currency = re.search(r"14\.Cur\s+([A-Z]{3})", text)
    term = re.search(r"15\.Term\s+([A-Z]{3})", text)
    inv_value = None
    i = _header_row(lines, "1.INV VALUE")
    if i is not None and i + 1 < len(lines):
        m = re.match(r"\s*(\d+(?:\.\d+)?)", lines[i + 1])
        inv_value = _num(m.group(1)) if m else None
    inv_no = re.search(r"INVOICE\s*\d+\s+([A-Z0-9][A-Z0-9/._-]{1,30})", text)
    supplier = None
    i = _header_row(lines, "3.SUPPLIER NAME")
    if i is not None and i + 1 < len(lines):
        supplier = re.split(r"\s{3,}", lines[i + 1].strip())[0] or None
    if supplier and supplier.upper().startswith("4."):
        supplier = None

    challan, paid_on = None, None
    i = _header_row(lines, "2.CHALLAN NO")
    if i is not None:
        start = max(0, lines[i].find("2.CHALLAN NO") - 6)              # the payment block is the right-hand half of the row
        for ln in lines[i + 1:i + 4]:
            m = re.search(r"\b(\d{7,15})\b(?:\s+(\d{2}/\d{2}/\d{4}))?", ln[start:])
            if m:
                challan, paid_on = m.group(1), _date(m.group(2))
                break
    country = re.search(r"COUNTRY OF CONSIGNMENT\s+([A-Z][A-Z ,.&-]*?)(?:\s{2,}|$)", text, re.M)
    assess = re.search(r"Assessment\s+(\d{2}-[A-Za-z]{3}-\d{2})", text)
    ooc = re.search(r"\bOOC\s+(\d{2}-[A-Za-z]{3}-\d{2})", text)

    assessable = duty.get("assessable")
    ccy = currency.group(1) if currency else (rate.group(1) if rate else None)
    rate_v = _num(rate.group(2)) if rate else None
    cif_foreign = None
    if assessable and rate_v:
        cif_foreign = (assessable / rate_v).quantize(Decimal("0.01"))
        if inv_value and abs(cif_foreign - inv_value) > Decimal("0.05") * max(Decimal(1), inv_value / 100):
            warnings.append(f"The assessable value works out to {ccy} {cif_foreign} but the invoice says {inv_value}; customs may have added freight, insurance or other charges. Check which is right.")
    elif inv_value:
        cif_foreign = inv_value
    if cif_foreign is None:
        warnings.append("The value of the goods could not be read; enter it from the bill")
    if rate_v is None:
        warnings.append("The exchange rate could not be read; enter the customs rate")

    bcd, sws, igst = duty.get("bcd", Decimal(0)), duty.get("sws", Decimal(0)), duty.get("igst", Decimal(0))
    total = duty.get("total")
    other = None
    if total is not None:
        other = total - bcd - sws - igst
        if other < 0:
            warnings.append("The duty figures do not add up; check them against the bill")
            other = Decimal(0)
    else:
        other = sum((duty.get(k, Decimal(0)) for k in ("acd", "nccd", "add", "cvd", "cess", "sg", "saed", "gsia", "tta", "health")), Decimal(0))
        warnings.append("The total duty could not be read; the figures shown are the parts that were found")
    paid_amount = duty.get("amount")
    if paid_amount is not None and total is not None and paid_amount != total:
        warnings.append(f"Total duty is {total} but the amount to pay is {paid_amount} (interest, penalty or fine may be included). Only the duty is recorded.")

    ooc_d, assess_d = _date(ooc.group(1)) if ooc else None, _date(assess.group(1)) if assess else None
    if ooc_d:
        status, paid = "cleared", paid_on or ooc_d
    elif paid_on:
        status, paid = "duty_paid", paid_on
    elif assess_d:
        status, paid = "assessed", None
    else:
        status, paid = "filed", None

    return {
        "boe_number": head.group(2), "boe_date": _date(head.group(3)), "port_code": head.group(1),
        "importer_name": importer, "importer_gstin": gstin.group(1) if gstin else None, "importer_iec": iec.group(1) if iec else None,
        "supplier_name": supplier, "supplier_country": COUNTRIES.get(country.group(1).strip().upper()) if country else None, "invoice_number": inv_no.group(1) if inv_no else None, "incoterm": term.group(1) if term else None,
        "currency": ccy, "cif_foreign": cif_foreign, "exchange_rate": rate_v,
        "bcd": bcd, "sws": sws, "igst": igst, "other_duty": other, "duty_total": total if total is not None else bcd + sws + igst + (other or Decimal(0)),
        "challan_number": challan, "status": status, "duty_paid_on": paid, "out_of_charge_on": ooc_d, "assessed_on": assess_d,
        "warnings": warnings,
    }
