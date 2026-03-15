from datetime import datetime
import re
import time
import os

import requests

def normalize_transaction(tx):
    required_fields = [
        "status", "date_of_transaction", "receiver_name", "receiver_bank",
        "message", "transaction_number", "sent_from", "utr",
        "receiver_phone_number", "amount", "upi_method", "confidence"
    ]

    normalized = {}

    # --- Date ---
    raw_date = tx.get("date_of_transaction")

    if raw_date:
        if isinstance(raw_date, str) and re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', raw_date):
            normalized["date_of_transaction"] = raw_date
        else:
            normalized["date_of_transaction"] = None
    else:
        normalized["date_of_transaction"] = None

    # --- Other fields ---
    for field in required_fields:
        if field == "date_of_transaction":
            continue

        val = tx.get(field)

        if field == "amount" and val is not None:
            try:
                val = float(val)
            except Exception:
                val = None
        
        # Normalize phone number to ensure +91 prefix
        if field == "receiver_phone_number" and val is not None:
            # Remove all non-digit characters
            phone_digits = re.sub(r'\D', '', str(val))
            
            if phone_digits:
                if len(phone_digits) == 10:
                    # 10 digits: add +91
                    val = "+91" + phone_digits
                elif len(phone_digits) == 11 and phone_digits.startswith("0"):
                    # 11 digits starting with 0: remove 0 and add +91
                    val = "+91" + phone_digits[1:]
                elif len(phone_digits) == 12 and phone_digits.startswith("91"):
                    # 12 digits starting with 91: add +
                    val = "+" + phone_digits
                elif len(phone_digits) == 13 and phone_digits.startswith("91"):
                    # 13 digits starting with 91: add +
                    val = "+" + phone_digits
                elif len(phone_digits) > 10:
                    # Other cases: take last 10 digits and add +91
                    val = "+91" + phone_digits[-10:]
                else:
                    # Less than 10 digits: set to None
                    val = None

        normalized[field] = val

    return normalized

def stringify_keys_but_keep_values(data_list):
    fixed = []
    for doc in data_list:
        new_doc = {}
        for k, v in doc.items():
            new_doc[str(k)] = v  # Convert key to string, leave value unchanged
        fixed.append(new_doc)
    return fixed



def process_transaction_date(date_str: str):
    """
    Converts a transaction date string like
    '02 : 10 pm on 01 Mar 2025' into:
      - date_of_transaction: 'dd/mm/yyyy'
      - time: 'HH:MM' (24-hr format)
    """

    # Normalize string (remove spaces around :)
    date_str = date_str.replace(" : ", ":").strip()

    # Example format: "02:10 pm on 01 Mar 2025"
    dt = datetime.strptime(date_str, "%I:%M %p on %d %b %Y")

    # Convert to desired formats
    formatted_date = dt.strftime("%d/%m/%Y")  # dd/mm/yyyy
    formatted_time = dt.strftime("%H:%M")     # 24 hr time

    return formatted_date, formatted_time

def get_lines(azure_json):
    lines = []
    read_results = azure_json["analyzeResult"]["readResults"]

    for page in read_results:
        for line in page["lines"]:
            lines.append(line["text"].strip())

    return lines


def callAzureOCR(image):

    azure_endpoint = os.getenv("azure_endpoint")
    azure_key = os.getenv("azure_key")
    analyze_url = f"{azure_endpoint}/vision/v3.2/read/analyze"

    headers = {
        "Ocp-Apim-Subscription-Key": azure_key,
        "Content-Type": "application/octet-stream"
    }

    response = requests.post(analyze_url, headers=headers, data=image)

    if response.status_code != 202:
        raise Exception(f"OCR request failed: {response.text}")

    operation_url = response.headers["Operation-Location"]

    timeout = 30  # seconds
    start_time = time.time()

    while True:

        if time.time() - start_time > timeout:
            raise Exception("OCR polling timeout")

        result_response = requests.get(
            operation_url,
            headers={"Ocp-Apim-Subscription-Key": azure_key}
        )

        result_json = result_response.json()
        status = result_json.get("status")

        if status == "succeeded":
            return result_json
        elif status == "failed":
            raise Exception("OCR processing failed")

        time.sleep(1)

def receiptClassifier(azure_json):

    for page in azure_json.get("analyzeResult", {}).get("readResults", []):
        for line in page.get("lines", []):

            line_text = line.get("text", "").strip()

            # Google Pay tick
            if line_text == "L":
                return "googlepay"

            # Axis detection
            if "Payment Complete" in line_text:
                return "axis"

            # PhonePe detection
            if "पे" in line_text:
                return "phonepe"

            for word in line.get("words", []):

                word_text = word.get("text", "")

                if word_text == "L":
                    return "googlepay"

                if word_text == "पे":
                    return "phonepe"

    return "unknown"

def redirectReceipt(azure_json, mode_of_receipt):
    if mode_of_receipt is 'phonepe':
        return phonepeReceiptParser(azure_json)
    if mode_of_receipt is 'googlepay':
        return gpayReceiptParser(azure_json)
    if mode_of_receipt is 'axis':
        return axisReceiptParser(azure_json)
    


class axisReceiptParser:

    AMOUNT_PATTERN = r'\d{1,3}(?:,\d{3})*(?:\.\d+)?'

    def __init__(self, azure_json):
        self.lines = self._flatten_lines(azure_json)

    def _flatten_lines(self, azure_json):
        lines = []
        for page in azure_json["analyzeResult"]["readResults"]:
            for line in page["lines"]:
                txt = line["text"].strip()
                if txt:
                    lines.append(txt)
        return lines

    def _normalize_amount(self, amt):
        cleaned = re.sub(r"[^\d.]", "", amt)
        return int(float(cleaned))

    def _normalize_date(self, date_str):
        dt = datetime.strptime(date_str, "%d/%m/%Y")
        return dt.strftime("%Y-%m-%d 00:00:00")

    def parse(self):

        data = {
            "status": "Successful",
            "date_of_transaction": None,
            "receiver_name": None,
            "amount": None,
            "upi_method": 'axis',
            "receiver_phone_number": None,
            "receiver_bank": None,
            "message": None,
            "transaction_number": None,
            "sent_from": None,
            "utr": None,
            "confidence": 1
        }

        # ---------- Receiver ----------
        for i, text in enumerate(self.lines):

            if text.upper() == "SENT TO":

                name_parts = []
                j = i + 1

                while j < len(self.lines):

                    nxt = self.lines[j]

                    if "XXXX" in nxt or "AMOUNT" in nxt:
                        break

                    name_parts.append(nxt)
                    j += 1

                if name_parts:
                    data["receiver_name"] = " ".join(name_parts)

        # ---------- Amount ----------
        for text in self.lines:

            if re.fullmatch(self.AMOUNT_PATTERN, text):
                data["amount"] = self._normalize_amount(text)
                break

        # ---------- Message ----------
        for i, text in enumerate(self.lines):

            if text.upper().startswith("REMARKS"):

                msg = []

                if i+1 < len(self.lines):
                    msg.append(self.lines[i+1])

                if i+2 < len(self.lines):
                    msg.append(self.lines[i+2])

                data["message"] = " ".join(msg)

        # ---------- Sent From ----------
        for i, text in enumerate(self.lines):

            if text.upper() == "SENT FROM" and i+1 < len(self.lines):
                data["sent_from"] = self.lines[i+1]

        # ---------- Receipt Number ----------
        for text in self.lines:

            if text.startswith("RECEIPT NO"):
                data["transaction_number"] = text.split(":")[-1].strip()

        # ---------- RRN ----------
        for i, text in enumerate(self.lines):

            if text.startswith("RRN") and i+1 < len(self.lines):
                data["utr"] = self.lines[i+1]

        # ---------- Date ----------
        for i, text in enumerate(self.lines):

            if text.startswith("DATE") and i+1 < len(self.lines):
                data["date_of_transaction"] = self._normalize_date(self.lines[i+1])

        return data

class gpayReceiptParser:

    RUPEE_PATTERN = r'₹\s?\d+(?:,\d+)*(?:\.\d+)?'
    PHONE_PATTERN = r'^(?:\+91[-\s]?)?[6-9]\d{9}$'
    UPI_PATTERN = r'[a-zA-Z0-9.\-_]{2,}@[a-zA-Z]{2,}'
    
    def __init__(self, azure_json):
        self.lines = self._flatten_lines(azure_json)

    def _flatten_lines(self, azure_json):
        lines = []
        read_results = azure_json["analyzeResult"]["readResults"]

        for page in read_results:
            for line in page["lines"]:
                text = line["text"].strip()
                if text:
                    lines.append(text)

        return lines


    def _normalize_amount(self, amount_str):
        cleaned = re.sub(r"[^\d.]", "", amount_str)
        return int(float(cleaned))


    def _normalize_date(self, date_str):

        date_str = date_str.replace(",", "")

        formats = [
            "%d %B %Y %I:%M %p",
            "%d %b %Y %I:%M %p",
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                pass

        return date_str


    def parse(self):

        data = {
            "status": "Successful",
            "date_of_transaction": None,
            "receiver_name": None,
            "amount": None,
            "upi_method": 'googlepay',
            "receiver_phone_number": None,
            "receiver_bank": None,
            "message": None,
            "transaction_number": None,
            "sent_from": None,
            "utr": None,
            "confidence": 1
        }

        amounts_found = []

        for i, text in enumerate(self.lines):

            # ---------- Amount ----------
            amt = re.search(self.RUPEE_PATTERN, text)
            if amt:
                raw = amt.group()
                amounts_found.append(raw)
                if not data["amount"]:
                    data["amount"] = self._normalize_amount(raw)


            # ---------- Paid To ----------
            if text.lower() == "paid to" and i + 1 < len(self.lines):

                data["receiver_name"] = self.lines[i+1]

                if i + 2 < len(self.lines):

                    next_line = self.lines[i+2]

                    # Extract UPI
                    upi = re.search(self.UPI_PATTERN, next_line)
                    if upi:
                        # data["upi_method"] = upi.group()

                        phone = re.search(r'\d{10}', upi.group())
                        if phone:
                            data["receiver_phone_number"] = "+91" + phone.group()

                    # Extract phone
                    phone = re.search(r'\d{10}', next_line)
                    if phone:
                        data["receiver_phone_number"] = "+91" + phone.group()


            # ---------- Date ----------
            if re.search(r'\d{1,2}\s+\w+\s+20\d{2}', text):

                cleaned = text.replace(",", "")
                data["date_of_transaction"] = self._normalize_date(cleaned)


        # -------- Confidence check --------
        unique_amounts = list(set(amounts_found))

        if len(unique_amounts) > 1:
            data["confidence"] = 0
        # print(data, 'here is object')
        return data
    

class phonepeReceiptParser:

    RUPEE_PATTERN = r'₹\s?\d+(?:,\d+)*(?:\.\d+)?'
    PHONE_PATTERN = r'^(?:\+91[-\s]?)?[6-9]\d{9}$'
    UPI_PATTERN = r'[Xx]+\d*@\w+'
    UTR_PATTERN = r'UTR[:\s]*([\d]+)'
    TXN_PATTERN = r'^T\d{10,}'

    def __init__(self, azure_json):
        self.lines = self._flatten_lines(azure_json)

    def _flatten_lines(self, azure_json):
        lines = []
        read_results = azure_json["analyzeResult"]["readResults"]
        for page in read_results:
            for line in page["lines"]:
                text = line["text"].strip()
                if text:
                    lines.append(text)
        return lines

    # ✅ NEW: Normalize amount
    def _normalize_amount(self, amount_str):
        if not amount_str:
            return None
        cleaned = re.sub(r"[^\d.]", "", amount_str)
        return int(float(cleaned))

    # ✅ NEW: Normalize date
    def _normalize_date(self, date_str):
        if not date_str:
            return None

        possible_formats = [
            "%I:%M %p on %d %b %Y",
            "%I:%M %p %d %b %Y",
            "%d %b %Y %I:%M %p",
        ]

        for fmt in possible_formats:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

        return date_str  # fallback if unknown format

    def parse(self):
        """
        Parse receipt and return data with database-compatible field names.
        Maps semantic fields to database schema:
        - date → date_of_transaction
        - receiver_name → receiver_name
        - receiver_phone → receiver_phone_number
        - receiver_bank → receiver_bank
        - receiver_upi → upi_method
        - transaction_id → transaction_number
        """
        data = {
            "status": None,
            "date_of_transaction": None,
            "receiver_name": None,
            "amount": None,
            "upi_method": 'phonepe',
            "receiver_phone_number": None,
            "receiver_bank": None,
            "message": None,
            "transaction_number": None,
            "sent_from": None,
            "utr": None,
            "confidence": 1
        }

        amounts_found = []

        for i, text in enumerate(self.lines):

            # Extract status and date
            if "Transaction Successful" in text:
                data["status"] = "Successful"
                if i + 1 < len(self.lines):
                    raw_date = self.lines[i + 1]
                    data["date_of_transaction"] = self._normalize_date(raw_date)

            # Extract receiver name and related info
            if text.lower() == "paid to" and i + 1 < len(self.lines):

                name_line = self.lines[i + 1]

                rupee_match = re.search(self.RUPEE_PATTERN, name_line)
                if rupee_match:
                    raw_amount = rupee_match.group()
                    amounts_found.append(raw_amount)
                    data["amount"] = self._normalize_amount(raw_amount)
                    name_line = name_line.split("₹")[0].strip()

                data["receiver_name"] = name_line

                if i + 2 < len(self.lines):
                    unknown1 = self.lines[i + 2]

                    rupee_match2 = re.search(self.RUPEE_PATTERN, unknown1)
                    if rupee_match2:
                        raw_amount = rupee_match2.group()
                        amounts_found.append(raw_amount)
                        data["amount"] = self._normalize_amount(raw_amount)

                    # elif re.search(self.UPI_PATTERN, unknown1):
                        # data["upi_method"] = unknown1

                    elif re.search(self.PHONE_PATTERN, unknown1):
                        data["receiver_phone_number"] = unknown1

                    elif not unknown1.startswith("+") and not unknown1.upper().startswith("X"):
                        data["receiver_name"] += " " + unknown1

            # Extract banking name
            if text.lower().startswith("banking na") and i + 1 < len(self.lines):
                candidate = self.lines[i + 1]
                if len(candidate) >= 3:
                    data["receiver_bank"] = candidate

            if text.lower() == "sent to" and i + 1 < len(self.lines):
                bank_line = self.lines[i + 1].replace(":", "").strip()
                if len(bank_line) >= 3:
                    data["receiver_bank"] = bank_line

            # Extract message
            if text.lower().startswith("message") and i + 1 < len(self.lines):
                data["message"] = self.lines[i + 1]

            # Extract transaction number
            if text.lower() == "transaction id" and i + 1 < len(self.lines):
                data["transaction_number"] = self.lines[i + 1]

            if re.match(self.TXN_PATTERN, text):
                data["transaction_number"] = text

            # Extract UTR
            utr_match = re.search(self.UTR_PATTERN, text)
            if utr_match:
                data["utr"] = utr_match.group(1)

            # Extract sent_from (account pattern like XXXXXXXXXXXI132)
            if re.match(r'^X{4,}\d+$', text):
                data["sent_from"] = text

            # Collect all amounts
            amount_match = re.search(self.RUPEE_PATTERN, text)
            if amount_match:
                amounts_found.append(amount_match.group())

            # Extract phone number
            phone_match = re.match(self.PHONE_PATTERN, text.strip())
            if phone_match:

                phone_digits = re.sub(r'\D', '', phone_match.group())

                valid_phone = None

                # Strict Indian validation
                if len(phone_digits) == 10 and phone_digits[0] in "6789":
                    valid_phone = "+91" + phone_digits

                elif len(phone_digits) == 12 and phone_digits.startswith("91") \
                        and phone_digits[2] in "6789":
                    valid_phone = "+" + phone_digits

                # ❗ Reject if:
                # - matches UTR
                # - too long (likely UTR)
                # - already stored as UTR
                if valid_phone:
                    if phone_digits != str(data.get("utr")) \
                            and len(phone_digits) <= 12:
                        data["receiver_phone_number"] = valid_phone

        # Handle amount if not yet set
        unique_amounts = list(set(amounts_found))

        if not data["amount"] and unique_amounts:
            data["amount"] = self._normalize_amount(unique_amounts[0])

        if len(unique_amounts) > 1:
            data["confidence"] = 0

        # Set default status if not found
        if not data["status"]:
            data["status"] = "Successful"

        return data