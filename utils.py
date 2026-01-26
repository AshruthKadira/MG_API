import json
import ast
import re
from datetime import datetime

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
            except ValueError:
                new_key = k
            new_dict[new_key] = v
        new_list.append(new_dict)

    return new_list


def transform_values_and_keys(dict_list):
    print(dict_list, 'before transformation')
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
                # Case 1: "Paid to John Doe"
                parts = v.split("Paid to", 1)
                if len(parts) > 1 and parts[1].strip():
                    new_dict['reciever_name'] = parts[1].strip()
                # Case 2: Separate key-value style (Paid to → Name)
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

            # Phone number logic (international and 10-digit)
            if isinstance(v, str):
                cleaned_v = re.sub(r'[^\d+]', '', v)
                try:
                    if cleaned_v.startswith('+') and len(re.sub(r'\D', '', cleaned_v)) >= 10:
                        digits_only = re.sub(r'\D', '', cleaned_v)
                        phone_num = int(digits_only[-10:])
                        new_dict['reciever_phone_number'] = phone_num
                        continue
                    elif cleaned_v.isdigit() and len(cleaned_v) == 10:
                        new_dict['reciever_phone_number'] = int(cleaned_v)
                        continue
                except:
                    new_dict[k] = v
                    continue


            if isinstance(v, str) and v.startswith(('₽', '€', '$', '₴', '3', '7', '2')) and "," in v:
                print(v, 'v', str, 'str')
                try:
                    value_num = float(v[1:]) if v[1:].replace('.', '', 1).isdigit() else v[1:]
                    new_dict['amount'] = value_num
                    amountAlreadyDefined = True
                except:
                    new_dict[k] = v
                continue

            elif isinstance(v, (int, float)) and (amountAlreadyDefined == False):
                print(v, 'v', str, 'str')
                new_dict['amount'] = float(str(v)[1:])
                continue

            new_dict[k] = v

        # Add UPI Method
        if new_dict.get('status') == 'Transaction Successful':
            new_dict['upi_method'] = 'PhonePe'
        else:
            new_dict['upi_method'] = 'Unknown'

        transformed.append(new_dict)

    return transformed

def normalize_transaction(tx):
    """
    Ensure that all keys required for DB insert exist, even if missing.
    Fill missing ones with None.
    Also process date_of_transaction into proper date + time.
    Clean and convert amount properly.
    """
    required_fields = [
        "status", "date_of_transaction", "time", "reciever_name", "banking_name",
        "message", "transaction_number", "sent_from", "utr",
        "reciever_phone_number", "amount", "upi_method"
    ]
    normalized = {}

    # --- Handle date_of_transaction specially ---
    raw_date = tx.get("date_of_transaction")
    if raw_date:
        try:
            formatted_date, formatted_time = process_transaction_date(raw_date)
            normalized["date_of_transaction"] = formatted_date
            normalized["time"] = formatted_time
        except Exception:
            # if parsing fails, keep original string and null time
            normalized["date_of_transaction"] = raw_date
            normalized["time"] = None
    else:
        normalized["date_of_transaction"] = None
        normalized["time"] = None

    # --- Handle other fields ---
    for field in required_fields:
        if field in ("date_of_transaction", "time"):
            continue

        val = tx.get(field)

        # Clean up "amount"
        if field == "amount" and val is not None:
            if isinstance(val, str):
                cleaned = val.strip().replace(",", "")
                # remove leading currency symbols like ₹ $ €
                cleaned = re.sub(r'^[^\d\-\.]+', '', cleaned)
                try:
                    val = float(cleaned)
                except Exception:
                    val = None
            elif isinstance(val, (int, float)):
                val = float(val)
            else:
                val = None

        normalized[field] = val
    print(normalized, 'normal')
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