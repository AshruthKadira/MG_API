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


class ReceiptParser:

    RUPEE_PATTERN = r'₹\s?\d+(?:,\d+)*(?:\.\d+)?'
    PHONE_PATTERN = r'(?:\+91[-\s]?)?[6-9]\d{9}'
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
            "upi_method": None,
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

                    elif re.search(self.UPI_PATTERN, unknown1):
                        data["upi_method"] = unknown1

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
            phone_match = re.search(self.PHONE_PATTERN, text)
            if phone_match:
                phone = re.sub(r'\D', '', phone_match.group())

                if len(phone) == 10:
                    phone = "+91" + phone
                elif len(phone) == 12 and phone.startswith("91"):
                    phone = "+" + phone

                data["receiver_phone_number"] = phone

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