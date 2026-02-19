# server/utils.py
import json
import ast
import re
from datetime import datetime
from typing import Optional

import cv2
import numpy as np
# ---------------------------------------------------------------------------
# Text-input route helpers (unchanged — used by the POST / route)
# ---------------------------------------------------------------------------

def change_content(x):
    cleaned_str = x.replace('}{', '},{')
    try:
        dict_list = json.loads(f"[{cleaned_str}]")
    except json.JSONDecodeError:
        try:
            dict_list = [ast.literal_eval(item + "}") for item in cleaned_str.strip('{}').split('},{')]
        except Exception as e:
            return {"error": f"Failed to parse data: {str(e)}"}, 400

    new_list = []
    for d in dict_list:
        new_dict = {}
        for k, v in d.items():
            try:
                new_key = int(k)
            except (ValueError, TypeError):
                new_key = k
            new_dict[new_key] = v
        new_list.append(new_dict)
    return new_list


def transform_values_and_keys(dict_list):
    transformed = []
    date_pattern = re.compile(
        r'''(?ix)
        (
            \d{1,2}:\d{2}
            \s?[ap]\.?m\.?
            \s?(on)?\s?
            \d{1,2}\s?[A-Za-z]{3,}
            \s?\d{4}
        )
        |
        (
            \d{1,2}\s+[A-Za-z]+
            \s+\d{4}
            \s+at\s+
            \d{1,2}:\d{2}
            \s?[APap]\.?M\.?
        )
        '''
    )

    for d in dict_list:
        new_dict = {}
        amountAlreadyDefined = False
        items = list(d.items())

        for i, (k, v) in enumerate(items):
            if isinstance(v, str) and v.isdigit():
                v = int(v)

            if k == 1:
                new_dict['status'] = v
                continue

            if isinstance(v, str) and date_pattern.match(v.strip()):
                new_dict['date_of_transaction'] = v.strip()
                continue

            if isinstance(v, str) and v.startswith("Paid to"):
                parts = v.split("Paid to", 1)
                if len(parts) > 1 and parts[1].strip():
                    new_dict['reciever_name'] = parts[1].strip()
                elif k + 1 in d:
                    new_dict['reciever_name'] = d[k + 1]
                continue

            if isinstance(v, str) and 'Banking Name :' in v:
                new_dict['banking_name'] = v.split('Banking Name :')[-1].strip()
                continue

            if isinstance(v, str) and 'Message' in v:
                new_dict['message'] = v.split('Message')[-1].strip(':').strip()
                continue

            if isinstance(v, str) and v.startswith('T25'):
                new_dict['transaction_number'] = v
                continue

            if isinstance(v, str) and v.startswith('XXXX'):
                new_dict['sent_from'] = v
                continue

            if isinstance(v, str) and v.startswith('UTR:'):
                new_dict['utr'] = v[5:]
                continue

            if isinstance(v, str):
                cleaned_v = re.sub(r'[^\d+]', '', v)
                try:
                    if cleaned_v.startswith('+') and len(re.sub(r'\D', '', cleaned_v)) >= 10:
                        digits_only = re.sub(r'\D', '', cleaned_v)
                        new_dict['reciever_phone_number'] = int(digits_only[-10:])
                        continue
                    elif cleaned_v.isdigit() and len(cleaned_v) == 10:
                        new_dict['reciever_phone_number'] = int(cleaned_v)
                        continue
                except Exception:
                    new_dict[k] = v
                    continue

            if isinstance(v, (int, float)) and not amountAlreadyDefined:
                new_dict['amount'] = float(v)
                amountAlreadyDefined = True
                continue

            new_dict[k] = v

        new_dict['upi_method'] = (
            'PhonePe' if new_dict.get('status') == 'Transaction Successful' else 'Unknown'
        )
        transformed.append(new_dict)

    return transformed


def stringify_keys_but_keep_values(data_list):
    return [{str(k): v for k, v in doc.items()} for doc in data_list]


# ---------------------------------------------------------------------------
# normalize_transaction
# Used by BOTH the image/LLM route and the text-input route.
# Accepts whatever shape the LLM or transform_values_and_keys returns and
# produces a clean dict that maps 1-to-1 onto the PostgreSQL columns.
# ---------------------------------------------------------------------------

# Columns that exist in the transactions table
_DB_FIELDS = [
    "status", "date_of_transaction", "reciever_name", "banking_name",
    "message", "transaction_number", "sent_from", "utr",
    "reciever_phone_number", "amount", "upi_method",
]


def normalize_transaction(tx: dict) -> dict:
    normalized = {}

    # ── date_of_transaction ───────────────────────────────────────────────
    normalized["date_of_transaction"] = _parse_date(tx.get("date_of_transaction"))

    # ── amount ────────────────────────────────────────────────────────────
    normalized["amount"] = _parse_amount(tx.get("amount"))

    # ── all other DB fields ───────────────────────────────────────────────
    for field in _DB_FIELDS:
        if field in ("date_of_transaction", "amount"):
            continue
        normalized[field] = _clean_val(tx.get(field))

    print(normalized, 'normalized')
    return normalized


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _clean_val(val):
    """Coerce empty / null-like values to Python None."""
    if val is None:
        return None
    if isinstance(val, str) and val.strip().lower() in ("", "null", "none", "n/a", "-"):
        return None
    return val


def _parse_amount(val) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        # Strip currency symbols, commas, spaces
        cleaned = re.sub(r'[^\d.]', '', val.replace(',', ''))
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None
    return None


def _parse_date(val) -> Optional[str]:
    """
    Accept any common date string and always return "YYYY-MM-DD HH:MM:SS" or None.

    The LLM is instructed to return ISO format, but we handle legacy formats
    from the text-input route too.
    """
    if not val:
        return None
    if isinstance(val, str) and val.strip().lower() in ("null", "none", ""):
        return None

    FORMATS = [
        "%Y-%m-%d %H:%M:%S",       # ISO — LLM output
        "%Y-%m-%dT%H:%M:%S",       # ISO with T
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%I:%M %p on %d %b %Y",    # "11:36 pm on 17 Feb 2026"
        "%d %b %Y %I:%M %p",
        "%I:%M%p on %d %b %Y",     # no space before am/pm
    ]
    s = str(val).strip().replace(" : ", ":")  # handle "02 : 10 pm" style

    for fmt in FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

    # Return as-is if it already contains a year (best-effort)
    return s if re.search(r'\d{4}', s) else None






def preprocess_receipt_from_bytes(image_bytes, apply_threshold=True, scale_factor=1.5):
    import cv2
    import numpy as np

    np_arr = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if image is None:
        raise ValueError("Invalid image")

    # Resize
    image = cv2.resize(
        image,
        None,
        fx=scale_factor,
        fy=scale_factor,
        interpolation=cv2.INTER_CUBIC
    )

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    if apply_threshold:
        processed = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            5
        )
    else:
        processed = gray

    # 🔥 IMPORTANT FIX
    if len(processed.shape) == 2:
        processed = cv2.cvtColor(processed, cv2.COLOR_GRAY2BGR)
    print('preprocess done')
    return processed


def parse_receipt_to_json(text_lines):
    """
    text_lines = list of OCR lines (already rupee-normalized)
    """

    full_text = "\n".join(text_lines)

    def safe_search(pattern, text, group=None):
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None

        # If explicit group given
        if group is not None:
            return match.group(group).strip()

        # If pattern has capturing groups, return first one
        if match.lastindex:
            return match.group(1).strip()

        # Otherwise return full match
        return match.group(0).strip()


    # ----------------------------
    # 1️⃣ Status
    # ----------------------------
    status = None
    if re.search(r"successful", full_text, re.IGNORECASE):
        status = "Successful"
    elif re.search(r"failed", full_text, re.IGNORECASE):
        status = "Failed"
    elif re.search(r"pending", full_text, re.IGNORECASE):
        status = "Pending"

    # ----------------------------
    # 2️⃣ Date
    # Example: 11:36 pm on 17 Feb 2026
    # ----------------------------
    date_raw = safe_search(r'(\d{1,2}:\d{2}\s?(?:am|pm)\s?on\s?\d{1,2}\s\w+\s\d{4})', full_text)

    date_of_transaction = None
    if date_raw:
        try:
            dt = datetime.strptime(date_raw, "%I:%M %p on %d %b %Y")
            date_of_transaction = dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            date_of_transaction = None

    # ----------------------------
    # 3️⃣ Receiver Name
    # Usually comes after "Paid to"
    # ----------------------------
    # reciever_name = None
    # for i, line in enumerate(text_lines):
    #     if "paid to" in line.lower() and i + 1 < len(text_lines):
    #         reciever_name = text_lines[i + 1]
    #         break

    # ----------------------------


    # ----------------------------
    # 5️⃣ Transaction ID
    # ----------------------------
    transaction_number = safe_search(r'\bT\d+\b', full_text)

    # ----------------------------
    # 6️⃣ UTR
    # ----------------------------
    utr = safe_search(r'UTR[:\s]+(\d+)', full_text)

    # ----------------------------
    # 7️⃣ Masked Account
    # ----------------------------
    sent_from = safe_search(r'\bX{3,}\d+\b', full_text)

    # ----------------------------
    # 8️⃣ Phone Number
    # ----------------------------
    # reciever_phone_number = safe_search(r'\b\d{10}\b', full_text)

    # ----------------------------
    # 9️⃣ UPI ID
    # ----------------------------
    upi_id = safe_search(r'\b[\w\.-]+@\w+\b', full_text)

    # ----------------------------
    # 🔟 UPI Method (basic detection)
    # ----------------------------
    upi_method = None
    if "phonepe" in full_text.lower():
        upi_method = "PhonePe"
    elif "paytm" in full_text.lower():
        upi_method = "Paytm"
    elif "gpay" in full_text.lower() or "google pay" in full_text.lower():
        upi_method = "GPay"

    # ----------------------------
    # 1️⃣1️⃣ Banking Name
    # ----------------------------
    banking_name = None
    bank_match = re.search(r'(HDFC|AXIS|ICICI|SBI|YES|PAYTM|KOTAK)\s+BANK', full_text, re.IGNORECASE)
    if bank_match:
        banking_name = bank_match.group(0)

    reciever_name, reciever_phone_number, amount = extract_receiver_and_amount(text_lines)
    # ----------------------------
    # 1️⃣2️⃣ Message (Optional)
    # ----------------------------
    message = None
    msg_match = re.search(r'Message[:\s]+(.+)', full_text, re.IGNORECASE)
    if msg_match:
        message = msg_match.group(1)

    # ----------------------------
    # Final JSON
    # ----------------------------
    return {
        "status": status,
        "date_of_transaction": date_of_transaction,
        "reciever_name": reciever_name,
        "banking_name": banking_name,
        "message": message,
        "transaction_number": transaction_number,
        "sent_from": sent_from,
        "utr": utr,
        "reciever_phone_number": reciever_phone_number,
        "amount": amount,
        "upi_method": upi_method,
        "upi_id": upi_id
    }

def extract_receiver_and_amount(text_lines):
    reciever_name = None
    reciever_phone_number = None
    amount = None

    # --------------------------
    # 1️⃣ Extract Receiver Name
    # --------------------------
    for i, line in enumerate(text_lines):
        if "paid to" in line.lower():

            name_parts = []

            # Look ahead max 5 lines
            for j in range(i + 1, min(i + 6, len(text_lines))):
                candidate = text_lines[j].strip()

                if not candidate:
                    continue

                # Skip numeric-only lines (like 74,000 or 8)
                if re.fullmatch(r'[₹\d,]+', candidate):
                    continue

                # Stop if we hit phone or new section
                if candidate.startswith('+') or "banking" in candidate.lower():
                    break

                # Only accept alphabetic words
                if re.search(r'[A-Za-z]', candidate):
                    name_parts.append(candidate)
                else:
                    break

            if name_parts:
                reciever_name = " ".join(name_parts)

            break

    # --------------------------
    # 2️⃣ Extract Phone
    # --------------------------
    for line in text_lines:
        if re.fullmatch(r'\+?\d{10,13}', line.strip()):
            reciever_phone_number = re.sub(r'\D', '', line)[-10:]
            break

    # --------------------------
    # 3️⃣ Extract Amount (Better Logic)
    # --------------------------
    for i, line in enumerate(text_lines):
        if "debited from" in line.lower():

            # Look next few lines
            for j in range(i + 1, min(i + 6, len(text_lines))):
                candidate = text_lines[j].strip()

                matches = re.findall(r'\b\d{1,3}(?:,\d{3})+\b', candidate)

                for m in matches:
                    value = float(m.replace(",", ""))

                    # Ignore unrealistic large OCR mistake like 74,000
                    if 1 <= value <= 1_00_00_000:
                        amount = value
                        return reciever_name, reciever_phone_number, amount

    # Fallback: take smallest formatted number > 0
    all_values = []
    for line in text_lines:
        matches = re.findall(r'\b\d{1,3}(?:,\d{3})+\b', line)
        for m in matches:
            val = float(m.replace(",", ""))
            if 1 <= val <= 1_00_00_000:
                all_values.append(val)

    if all_values:
        amount = min(all_values)  # usually correct amount

    return reciever_name, reciever_phone_number, amount

def preprocess_receipt_for_rupee(image_bytes):

    np_arr = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    # 1️⃣ Resize bigger (important)
    image = cv2.resize(image, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)

    # 2️⃣ Convert to grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # 3️⃣ Invert (dark UI → light text)
    gray = cv2.bitwise_not(gray)

    # 4️⃣ Sharpen (helps thin ₹ strokes)
    kernel = np.array([[0, -1, 0],
                       [-1, 5, -1],
                       [0, -1, 0]])
    sharp = cv2.filter2D(gray, -1, kernel)

    # 5️⃣ Convert back to 3-channel for Paddle
    sharp = cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR)

    return sharp
