# server/tesUtils.py
"""
PhonePe receipt parser.

Accepts:
  word_data  – list[dict] from pytesseract (text, x, y, w, h, conf)
  full_text  – raw string from pytesseract image_to_string (optional but preferred)

Uses full_text as the primary source (better word joining) and word_data for
coordinate-based heuristics where needed.
"""

import re
from datetime import datetime
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MONTHS = r'Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec'

# UPI domains used in India
UPI_DOMAINS = [
    # PhonePe
    'ybl', 'axl', 'ibl',
    # Paytm — ptyes is Paytm's primary VPA domain
    'paytm', 'ptyes', 'pthdfc', 'ptsbi', 'ptaxis', 'ptkotak',
    # Google Pay
    'okicici', 'okhdfcbank', 'okaxis', 'oksbi', 'okbizaxis',
    # Banks direct
    'hdfcbank', 'icici', 'sbi', 'upi', 'fbl', 'idfcbank',
    'rbl', 'kotak', 'indus', 'airtel', 'apl', 'ikwik',
    'apb', 'barodampay', 'cnrb', 'csbpay', 'dbs', 'dlb',
    'ezeepay', 'freecharge', 'idbi', 'jkb', 'jsb', 'karb',
    'kvb', 'lvb', 'mahb', 'myicici', 'obc', 'pingpay', 'pnb',
    'sc', 'scmobile', 'tjsb', 'ubi', 'uboi', 'unionbank',
    'united', 'utbi', 'vijb', 'yapl',
]

# Known UPI apps / banking name keywords (longest match first to avoid
# 'pay' matching inside 'amazon pay')
UPI_APP_KEYWORDS = [
    'amazon pay', 'amazonpay',
    'google pay', 'gpay',
    'phone pe', 'phonepe',
    'axis bank', 'hdfc bank', 'icici bank',
    'paytm', 'bhim',
]

CURRENCY_SYMBOLS = r'₹|\u20b9|Rs\.?|INR'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_text(t: str) -> str:
    """Strip leading/trailing whitespace and normalise internal spaces."""
    return re.sub(r'\s+', ' ', t).strip()


def _lines(full_text: str) -> List[str]:
    return [_clean_text(ln) for ln in full_text.splitlines() if _clean_text(ln)]


def _words_from_full_text(full_text: str) -> List[str]:
    return full_text.split()


def _find_line_containing(pattern: str, lines: List[str], flags=re.IGNORECASE) -> Optional[int]:
    """Return index of first line matching pattern, or None."""
    for i, ln in enumerate(lines):
        if re.search(pattern, ln, flags):
            return i
    return None


def _next_nonempty_line(lines: List[str], after: int) -> Optional[str]:
    for ln in lines[after + 1:]:
        if ln:
            return ln
    return None


# ---------------------------------------------------------------------------
# Field extractors  (each returns the extracted value or None)
# ---------------------------------------------------------------------------

def _extract_status(lines: List[str]) -> Optional[str]:
    for ln in lines:
        if re.search(r'\bsuccessful\b', ln, re.IGNORECASE):
            return 'Successful'
        if re.search(r'\bfailed\b', ln, re.IGNORECASE):
            return 'Failed'
        if re.search(r'\bpending\b', ln, re.IGNORECASE):
            return 'Pending'
    return None


def _extract_date(full_text: str) -> Optional[str]:
    """
    Handles formats like:
      "02:10 pm on 01 Mar 2025"
      "09:29 pm 26 Aug 2025"
      "26 Aug 2025, 09:29 PM"
      "2025-08-26 09:29"
    Returns ISO datetime string YYYY-MM-DD HH:MM:SS
    """
    text = _clean_text(full_text)

    patterns = [
        # "02:10 pm on 01 Mar 2025"  or  "02 : 10 pm on 01 Mar 2025"
        (
            r'(\d{1,2})\s*:\s*(\d{2})\s*(am|pm)\s+on\s+(\d{1,2})\s+(' + MONTHS + r')\s+(\d{4})',
            lambda m: datetime.strptime(
                f"{m.group(4)} {m.group(5)} {m.group(6)} {m.group(1)}:{m.group(2)} {m.group(3).upper()}",
                "%d %b %Y %I:%M %p"
            )
        ),
        # "09:29 pm 26 Aug 2025"  (no "on")
        (
            r'(\d{1,2})\s*:\s*(\d{2})\s*(am|pm)\s+(\d{1,2})\s+(' + MONTHS + r')\s+(\d{4})',
            lambda m: datetime.strptime(
                f"{m.group(4)} {m.group(5)} {m.group(6)} {m.group(1)}:{m.group(2)} {m.group(3).upper()}",
                "%d %b %Y %I:%M %p"
            )
        ),
        # "26 Aug 2025, 09:29 PM"  or  "26 Aug 2025 09:29 PM"
        (
            r'(\d{1,2})\s+(' + MONTHS + r')\s+(\d{4})[,\s]+(\d{1,2}):(\d{2})\s*(am|pm)',
            lambda m: datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)} {m.group(4)}:{m.group(5)} {m.group(6).upper()}",
                "%d %b %Y %I:%M %p"
            )
        ),
        # ISO-ish: "2025-08-26 09:29"
        (
            r'(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})',
            lambda m: datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5))
            )
        ),
    ]

    for pattern, parser in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                dt = parser(m)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
    return None


def _is_date_or_time_line(line: str) -> bool:
    """
    Returns True if this line is primarily a date/time expression.
    Used to prevent date components from being picked up as amounts.

    Catches patterns like:
      "09:29 pm 26 Aug 2025"
      "26 Aug 2025"
      "2025-08-26"
      "26/08/2025"
      "on 01 Mar 2025"
    """
    date_signals = [
        re.search(MONTHS, line, re.IGNORECASE),           # contains a month name
        re.search(r'\b(am|pm)\b', line, re.IGNORECASE),  # contains am / pm
        re.search(r'\d{1,2}:\d{2}', line),                # contains HH:MM time
        re.search(r'\d{4}-\d{2}-\d{2}', line),            # ISO date
        re.search(r'\b\d{1,2}/\d{1,2}/\d{4}\b', line),   # DD/MM/YYYY
    ]
    return any(date_signals)


def _is_noise_number(raw: str, val: float) -> bool:
    """
    Returns True if a number should be excluded from amount candidates.

    Excludes:
      - Years: 1900-2099
      - Phone number fragments: 6-10 digit numbers starting with 6-9
      - UTR / transaction id fragments: 10+ digits
      - Day-of-month values that appear alongside month names (handled
        at the line level by _is_date_or_time_line)
      - Single or double digit numbers that are likely day/hour values
    """
    digits_only = raw.replace(',', '')

    # 4-digit year
    if re.match(r'^(19|20)\d{2}$', digits_only):
        return True

    # 10+ digit number → UTR, transaction ID, or phone number fragment
    if len(digits_only) >= 10:
        return True

    # 6–9 digit number starting with Indian mobile prefixes (6,7,8,9)
    if len(digits_only) in (6, 7, 8, 9) and digits_only[0] in '6789':
        return True

    # Tiny values (< 10) are almost never transaction amounts on receipts
    if val < 10:
        return True

    return False


def _extract_amount(full_text: str, lines: List[str],
                    word_data: List[Dict] = None) -> Optional[float]:
    """
    Extract the transaction amount.

    Priority order:
      1. ₹ / Rs / INR symbol directly before a number  ← most reliable
      2. Height-based: the number whose OCR bounding box is tallest
         (amount is always shown in large bold font on PhonePe receipts)
      3. Solo number line that passes all noise/date filters
      4. Comma-formatted number on a non-date line

    Passes a number through _is_noise_number() and _is_date_or_time_line()
    at every stage to avoid picking up years, UTR digits, or date parts.
    """

    # ── Priority 1: currency symbol prefix ────────────────────────────────
    currency_pattern = re.compile(
        r'(?:' + CURRENCY_SYMBOLS + r')\s*([\d,]+(?:\.\d{1,2})?)',
        re.IGNORECASE
    )
    for ln in lines:
        m = currency_pattern.search(ln)
        if m:
            try:
                val = float(m.group(1).replace(',', ''))
                if val >= 1 and not _is_noise_number(m.group(1), val):
                    return val
            except ValueError:
                pass

    # ── Priority 2: tallest number in word_data ────────────────────────────
    # The transaction amount is rendered in a large font — its bounding-box
    # height (h) will be noticeably bigger than ordinary body text (~20-30 px
    # for body, ~50-80 px for the amount on a standard screenshot).
    # We score each numeric detection by height and pick the tallest one that
    # is a plausible amount.
    if word_data:
        # Compute median height so we can express "tallest" as a ratio
        heights = [w['h'] for w in word_data if w['h'] > 0]
        if heights:
            heights_sorted = sorted(heights)
            median_h = heights_sorted[len(heights_sorted) // 2]

            # Collect (height, value) for every word that looks numeric
            tall_candidates = []
            for w in word_data:
                token = w['text'].strip()
                # Strip any leading currency symbol OCR might have kept
                token_clean = re.sub(r'^(?:' + CURRENCY_SYMBOLS + r')\s*',
                                     '', token, flags=re.IGNORECASE)
                # Must be a number (digits + optional comma/decimal)
                if not re.match(r'^[\d,]+(?:\.\d{1,2})?$', token_clean):
                    continue
                try:
                    val = float(token_clean.replace(',', ''))
                except ValueError:
                    continue
                if val < 1 or _is_noise_number(token_clean, val):
                    continue
                # Must be taller than 1.5× the median line height
                if w['h'] >= median_h * 1.5:
                    tall_candidates.append((w['h'], val))

            if tall_candidates:
                # Return the value belonging to the tallest detection
                tall_candidates.sort(reverse=True)
                return tall_candidates[0][1]

    # ── Priority 3: solo number line ───────────────────────────────────────
    # A line that contains NOTHING but a number — but we now also check the
    # surrounding lines for date context to catch split date tokens like "17"
    # that appear alone after EasyOCR separates "17 Feb 2026".
    solo_pattern = re.compile(r'^\s*([\d,]+(?:\.\d{1,2})?)\s*$')
    for idx, ln in enumerate(lines):
        if _is_date_or_time_line(ln):
            continue
        m = solo_pattern.match(ln)
        if not m:
            continue
        raw = m.group(1)
        try:
            val = float(raw.replace(',', ''))
        except ValueError:
            continue
        if val < 1 or _is_noise_number(raw, val):
            continue

        # Extra guard: if the line immediately before or after contains a
        # month name / am / pm, this token is very likely a date component
        # that EasyOCR split onto its own line.
        ctx_before = lines[idx - 1] if idx > 0 else ''
        ctx_after  = lines[idx + 1] if idx + 1 < len(lines) else ''
        if _is_date_or_time_line(ctx_before) or _is_date_or_time_line(ctx_after):
            continue

        return val

    # ── Priority 4: comma-formatted number on a safe line ─────────────────
    comma_pattern = re.compile(r'\b(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?)\b')
    for ln in lines:
        if _is_date_or_time_line(ln):
            continue
        m = comma_pattern.search(ln)
        if m:
            try:
                val = float(m.group(1).replace(',', ''))
                if val >= 1 and not _is_noise_number(m.group(1), val):
                    return val
            except ValueError:
                pass

    return None


def _strip_amount_from_name(text: str) -> str:
    """
    Remove a trailing ₹amount token from a name string.
    e.g. "Samsuddin Carpenter ₹300" → "Samsuddin Carpenter"
    """
    return re.sub(
        r'\s*(?:' + CURRENCY_SYMBOLS + r')\s*[\d,]+(?:\.\d{1,2})?\s*$',
        '',
        text,
        flags=re.IGNORECASE,
    ).strip()


def _looks_like_name(text: str) -> bool:
    """
    Return True if a line looks like a person/business name.
    Rejects: UPI IDs, pure numbers, currency lines, date lines.
    """
    t = _strip_amount_from_name(text)
    if not t:
        return False
    if re.search(r'@', t):           # UPI ID
        return False
    if re.search(r'^\d+$', t):       # pure number
        return False
    if _is_date_or_time_line(t):     # date/time line
        return False
    if re.search(                    # known UI labels
        r'^\s*(paid to|sent to|transfer details|transaction id|'
        r'debited from|utr|powered by|paytm|phonepe|gpay|bhim)\s*$',
        t, re.IGNORECASE
    ):
        return False
    return True


def _extract_receiver_name(lines: List[str]) -> Optional[str]:
    """
    Extracts the receiver name which appears after a 'Paid to' label.

    Handles three real-world layouts from PhonePe:

    Layout A — name only on next line:
        Paid to
        Rahul Sharma
        rahulsharma@ybl

    Layout B — name + amount inline (this receipt's format):
        Paid to
        Samsuddin Carpenter ₹300    ← amount stripped from end
        Ramesh Bai                  ← second line of name collected too
        XXXXXX3997@ptyes

    Layout C — name inline after label:
        Paid to Rahul Sharma
    """
    for i, ln in enumerate(lines):
        if not re.search(r'paid\s+to', ln, re.IGNORECASE):
            continue

        # Layout C: inline after label
        m = re.search(r'paid\s+to\s+(.+)', ln, re.IGNORECASE)
        if m:
            inline = _strip_amount_from_name(_clean_text(m.group(1)))
            if _looks_like_name(inline):
                return inline

        # Layouts A & B: name is on the line(s) after "Paid to"
        name_parts = []
        for nxt_ln in lines[i + 1:]:
            cleaned = _strip_amount_from_name(_clean_text(nxt_ln))
            if _looks_like_name(cleaned):
                name_parts.append(cleaned)
            else:
                # Stop as soon as we hit a non-name line (UPI ID, amount, label)
                break

        if name_parts:
            return ' '.join(name_parts)

    return None


def _extract_upi_id(lines: List[str]) -> Optional[str]:
    """
    Detect VPA / UPI ID.

    Matches both real and masked UPI IDs:
      real:   rahulsharma@ybl
      masked: XXXXXX3997@ptyes  (PhonePe masks the handle with X's)
    """
    upi_pattern = re.compile(
        r'[\w.\-+X]+@(?:' + '|'.join(UPI_DOMAINS) + r')\b',
        re.IGNORECASE
    )
    for ln in lines:
        m = upi_pattern.search(ln)
        if m:
            return m.group(0).strip()
    return None


def _extract_phone(lines: List[str]) -> Optional[str]:
    """
    Detects:
      - 10-digit Indian mobile numbers
      - +91 prefixed numbers
    """
    for ln in lines:
        # International format  +91XXXXXXXXXX
        m = re.search(r'\+91[\s\-]?([6-9]\d{9})', ln)
        if m:
            return m.group(1)
        # Plain 10-digit starting with 6-9
        m = re.search(r'\b([6-9]\d{9})\b', ln)
        if m:
            return m.group(1)
    return None


def _extract_transaction_id(lines: List[str]) -> Optional[str]:
    """
    Extract PhonePe transaction ID.

    Format: T followed by 20–24 digits  e.g. T2602172336276033093573

    Handles three OCR failure modes:
    1. Clean single token:  "T2602172336276033093573"
    2. Space split:         "T260217233627603309 3573"   → joined
    3. Line split:          line N  = "T260217233627603309"
                            line N+1 = "3573"            → joined
    """
    for i, ln in enumerate(lines):
        # Collect all digit-like characters from this line that start with T
        # Allow internal spaces so "T260... 3573" is captured in one pass
        m = re.search(r'\b(T[\d\s]{18,})', ln, re.IGNORECASE)
        if m:
            candidate = m.group(1).replace(' ', '').strip()
            # If the line ends mid-number, check next line for a pure-digit suffix
            if i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if re.match(r'^\d{1,6}$', nxt):       # small digit suffix
                    candidate += nxt
            if len(candidate) >= 20:                   # PhonePe IDs are 20-24 chars
                return candidate

    return None


def _extract_utr(lines: List[str]) -> Optional[str]:
    """UTR is a 12-digit number, often preceded by 'UTR'."""
    for i, ln in enumerate(lines):
        if re.search(r'\butr\b', ln, re.IGNORECASE):
            # Check inline
            m = re.search(r'\b(\d{10,15})\b', ln)
            if m:
                return m.group(1)
            # Check next line
            nxt = _next_nonempty_line(lines, i)
            if nxt:
                m = re.search(r'\b(\d{10,15})\b', nxt)
                if m:
                    return m.group(1)
    return None


def _extract_banking_name(lines: List[str]) -> Optional[str]:
    """
    Extract the bank or UPI app name.

    Sources checked in priority order:
    1. 'Banking Name : HDFC Bank'  — explicit label (older PhonePe layout)
    2. 'Bank : Axis'               — short form
    3. 'Sent to : paytm'           — newer PhonePe layout; the value IS the bank/app
    4. 'Powered by ... AXIS BANK'  — logo text at bottom of receipt
    """
    for i, ln in enumerate(lines):
        # Source 1
        m = re.search(r'banking\s+name\s*[:\-]?\s*(.*)', ln, re.IGNORECASE)
        if m:
            inline = _clean_text(m.group(1))
            return inline if inline else _next_nonempty_line(lines, i)

        # Source 2
        m = re.search(r'\bbank\s*[:\-]\s*(.*)', ln, re.IGNORECASE)
        if m:
            val = _clean_text(m.group(1))
            if val:
                return val

        # Source 3 — "Sent to : paytm" → "Paytm"
        m = re.search(r'sent\s+to\s*[:\-]?\s*(.*)', ln, re.IGNORECASE)
        if m:
            val = _clean_text(m.group(1))
            # strip bullet/dot prefixes that PhonePe sometimes adds
            val = re.sub(r'^[•\-\*\.\s]+', '', val).strip()
            if val and not re.search(r'@', val):   # skip if it's a UPI ID
                return val.title()                  # "paytm" → "Paytm"

    # Source 4 — "Powered by UPI AXIS BANK" at bottom
    for ln in lines:
        if re.search(r'powered\s+by', ln, re.IGNORECASE):
            # Known bank names that appear as logo text
            bank_match = re.search(
                r'\b(axis\s+bank|hdfc\s+bank|icici\s+bank|sbi|kotak|'
                r'yes\s+bank|bob|canara|union\s+bank|pnb|idfc)\b',
                ln, re.IGNORECASE
            )
            if bank_match:
                return bank_match.group(1).title()
            # "Powered by" on its own line — check next line
            nxt = _next_nonempty_line(lines, lines.index(ln))
            if nxt:
                bank_match = re.search(
                    r'\b(axis\s+bank|hdfc\s+bank|icici\s+bank|sbi|kotak|'
                    r'yes\s+bank|bob|canara|union\s+bank|pnb|idfc)\b',
                    nxt, re.IGNORECASE
                )
                if bank_match:
                    return bank_match.group(1).title()

    return None


def _extract_message(lines: List[str]) -> Optional[str]:
    """Looks for 'Message :' or 'Note :' patterns."""
    for i, ln in enumerate(lines):
        m = re.search(r'(?:message|note)\s*[:\-]?\s*(.*)', ln, re.IGNORECASE)
        if m:
            inline = _clean_text(m.group(1))
            if inline:
                return inline
            nxt = _next_nonempty_line(lines, i)
            return nxt
    return None


def _extract_sent_from(lines: List[str]) -> Optional[str]:
    """
    Extract the masked account number the payment was debited from.

    Format on receipt: XXXX9562  or  XXXXXXXXXXXI132

    Strategy:
    1. Prefer the line that comes directly after a 'Debited from' label
       (most precise — avoids confusing receiver UPI ID with account number)
    2. Fall back to any X-masked token that is NOT a UPI ID (no '@')
    """
    # Priority 1 — after "Debited from" label
    for i, ln in enumerate(lines):
        if re.search(r'debited\s+from', ln, re.IGNORECASE):
            for nxt in lines[i + 1:]:
                m = re.search(r'\b(X{4,}[\dA-Z]+)\b', nxt, re.IGNORECASE)
                if m:
                    return m.group(1)
            break

    # Priority 2 — any masked account token not on a UPI ID line
    for ln in lines:
        if '@' in ln:          # skip UPI ID lines like XXXXXX3997@ptyes
            continue
        m = re.search(r'\b(X{4,}[\dA-Z]+)\b', ln, re.IGNORECASE)
        if m:
            return m.group(1)

    return None


def _extract_upi_method(lines: List[str], status: Optional[str]) -> str:
    """
    Determine which UPI app/bank was used for the transfer.

    Priority:
    1. 'Sent to : paytm' label  — the most explicit signal on PhonePe receipts
    2. UPI app keyword anywhere in text  (longest keywords checked first to
       prevent 'pay' inside 'amazon pay' matching prematurely)
    3. Default to 'PhonePe' for successful transactions
    """
    UPI_APPS_ONLY = [
        'amazon pay', 'amazonpay',
        'google pay', 'gpay',
        'phone pe', 'phonepe',
        'paytm', 'bhim',
    ]

    # Priority 1 — "Sent to" label: the value IS the UPI app
    for ln in lines:
        m = re.search(r'sent\s+to\s*[:\-]?\s*(.*)', ln, re.IGNORECASE)
        if m:
            val = _clean_text(m.group(1)).lower()
            val = re.sub(r'^[•\-\*\.\s]+', '', val).strip()
            for app in UPI_APPS_ONLY:
                if app in val:
                    return app.title()

    # Priority 2 — keyword scan (UPI apps only — skip bank names here
    # because 'Axis Bank' in the Powered-by logo should not override the app)
    full = ' '.join(lines).lower()
    for app in UPI_APPS_ONLY:
        if app in full:
            return app.title()

    # Default
    return 'PhonePe' if status == 'Successful' else 'Unknown'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def clean_phonepe_transaction(
    word_data: List[Dict],
    full_text: str = ""
) -> Dict:
    """
    Parse a PhonePe transaction receipt from OCR output.

    Args:
        word_data:  List of word-level dicts (text, x, y, w, h, [conf])
        full_text:  Raw string output from pytesseract.image_to_string

    Returns:
        Dict with all transaction fields (None for undetected fields)
    """

    # Build full_text from word_data if not supplied
    if not full_text and word_data:
        sorted_words = sorted(word_data, key=lambda w: (w['y'], w['x']))
        full_text = ' '.join(w['text'] for w in sorted_words)

    lines = _lines(full_text)

    result = {
        'status':               _extract_status(lines),
        'date_of_transaction':  _extract_date(full_text),
        'reciever_name':        _extract_receiver_name(lines),
        'banking_name':         _extract_banking_name(lines),
        'message':              _extract_message(lines),
        'transaction_number':   _extract_transaction_id(lines),
        'sent_from':            _extract_sent_from(lines),
        'utr':                  _extract_utr(lines),
        'reciever_phone_number':_extract_phone(lines),
        'amount':               _extract_amount(full_text, lines, word_data),
        'upi_method':           None,   # filled below
        'upi_id':               _extract_upi_id(lines),
    }

    result['upi_method'] = _extract_upi_method(lines, result['status'])

    # If no receiver name found from "Paid to" but we have a UPI ID, the
    # part before @ is often the receiver handle (better than nothing)
    if not result['reciever_name'] and result['upi_id']:
        result['reciever_name'] = result['upi_id'].split('@')[0]

    return result


# ---------------------------------------------------------------------------
# Quick smoke-test
# ---------------------------------------------------------------------------

def _test():
    # Simulates what EasyOCR actually outputs:
    #   - ₹ dropped from "₹300" → bare "300" token
    #   - "17 Feb 2026" split into separate detections: "17", "Feb 2026"
    #   - word_data includes bounding-box heights to distinguish amount font size

    # The amount "300" appears in a large bold font (h≈70px).
    # The date "17" appears in small text (h≈25px).
    sample_word_data = [
        {"text": "Transaction Successful", "x": 100, "y": 10,  "w": 400, "h": 40,  "conf": 0.98},
        {"text": "11:36 pm on",            "x": 100, "y": 60,  "w": 200, "h": 25,  "conf": 0.95},
        {"text": "17",                     "x": 310, "y": 60,  "w": 30,  "h": 25,  "conf": 0.94},
        {"text": "Feb 2026",               "x": 345, "y": 60,  "w": 120, "h": 25,  "conf": 0.96},
        {"text": "Paid to",                "x": 100, "y": 120, "w": 120, "h": 30,  "conf": 0.97},
        {"text": "Samsuddin Carpenter",    "x": 100, "y": 170, "w": 300, "h": 35,  "conf": 0.95},
        {"text": "300",                    "x": 700, "y": 165, "w": 100, "h": 70,  "conf": 0.97},  # large font ← real amount
        {"text": "Ramesh Bai",             "x": 100, "y": 215, "w": 180, "h": 30,  "conf": 0.96},
        {"text": "XXXXXX3997@ptyes",       "x": 100, "y": 260, "w": 280, "h": 25,  "conf": 0.93},
        {"text": "Sent to",               "x": 100, "y": 330, "w": 100, "h": 25,  "conf": 0.94},
        {"text": "paytm",                  "x": 260, "y": 330, "w": 80,  "h": 25,  "conf": 0.98},
        {"text": "XXXXXX3997@ptyes",       "x": 160, "y": 370, "w": 280, "h": 25,  "conf": 0.92},
        {"text": "Transaction ID",         "x": 100, "y": 460, "w": 220, "h": 25,  "conf": 0.97},
        {"text": "T2602172336276033093573","x": 100, "y": 500, "w": 520, "h": 30,  "conf": 0.96},
        {"text": "Debited from",           "x": 100, "y": 570, "w": 180, "h": 25,  "conf": 0.96},
        {"text": "XXXX9562",              "x": 100, "y": 610, "w": 160, "h": 30,  "conf": 0.95},
        {"text": "300",                    "x": 700, "y": 610, "w": 90,  "h": 30,  "conf": 0.97},  # smaller instance
        {"text": "UTR: 503615410187",      "x": 100, "y": 660, "w": 380, "h": 25,  "conf": 0.96},
        {"text": "Powered by",             "x": 280, "y": 750, "w": 180, "h": 22,  "conf": 0.94},
        {"text": "UPI AXIS BANK",          "x": 200, "y": 780, "w": 260, "h": 22,  "conf": 0.91},
    ]

    # Build full_text from word_data (as routes.py does)
    sample_full_text = "\n".join(w["text"] for w in sample_word_data)

    result = clean_phonepe_transaction(sample_word_data, sample_full_text)
    print("\n=== Parsed Result ===")
    for k, v in result.items():
        status = "✅" if v is not None else "❌"
        print(f"  {status} {k:<28} {v}")

    # Confirm amount is correct
    assert result['amount'] == 300.0, f"FAIL: amount={result['amount']}, expected 300.0"
    assert result['status'] == 'Successful'
    assert result['utr'] == '503615410187'
    assert result['sent_from'] == 'XXXX9562'
    assert '2026-02-17' in result['date_of_transaction']
    print("\n✅ All assertions passed")


if __name__ == "__main__":
    _test()