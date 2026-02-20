# server/routes.py
from flask import Blueprint, request, jsonify
from utils import change_content, transform_values_and_keys, stringify_keys_but_keep_values, normalize_transaction
from db_config import get_connection
from utils import ReceiptParser, callAzureOCR

import io


routes = Blueprint('routes', __name__)

@routes.route('/', methods=['POST'])
def home():
    data = request.get_json()
    if 'data' not in data:
        return {"error": "Missing 'data' key"}, 400

    array_str = data['data'].replace("\n", "")
    parsed_data = change_content(array_str)

    # handle error correctly
    if isinstance(parsed_data, tuple) and "error" in parsed_data[0]:
        return parsed_data

    transformed = transform_values_and_keys(parsed_data)
    print(transformed, 'TRANSFORMED')
    safe_to_insert = stringify_keys_but_keep_values(transformed)
    print(safe_to_insert, 'SAFE TO INSERT')
    try:
        conn = get_connection()
        cur = conn.cursor()

        for tx in safe_to_insert:
            normalized_tx = normalize_transaction(tx)
            cur.execute("""
                INSERT INTO transactions (
                    status, date_of_transaction, receiver_name, banking_name,
                    message, transaction_number, sent_from, utr,
                    receiver_phone_number, amount, upi_method
                ) VALUES (
                    %(status)s, %(date_of_transaction)s, %(receiver_name)s, %(banking_name)s,
                    %(message)s, %(transaction_number)s, %(sent_from)s, %(utr)s,
                    %(receiver_phone_number)s, %(amount)s, %(upi_method)s
                )
            """, normalized_tx)

        conn.commit()
        return jsonify({
            "message": "Data stored in PostgreSQL",
            "data": safe_to_insert
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()

@routes.route("/extract-receipt", methods=["POST"])
def extract_receipt():

    image = request.data

    if not image:
        return jsonify({"error": "No image uploaded"}), 400

    try:
        azure_json = callAzureOCR(image)

        # Defensive check
        if not azure_json or "analyzeResult" not in azure_json:
            return jsonify({"error": "Invalid OCR response"}), 500

        parser = ReceiptParser(azure_json)
        result = parser.parse()

        # Normalize the transaction data for database insertion
        normalized_tx = normalize_transaction(result)
        
        # Insert into database
        conn = get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO transactions (
                date_of_transaction, receiver_name, receiver_bank,
                message, transaction_number, sent_from, utr,
                receiver_phone_number, amount, upi_method
            ) VALUES (
                %(date_of_transaction)s, %(receiver_name)s, %(receiver_bank)s,
                %(message)s, %(transaction_number)s, %(sent_from)s, %(utr)s,
                %(receiver_phone_number)s, %(amount)s, %(upi_method)s
            )
        """, normalized_tx)
        
        conn.commit()
        
        return jsonify({
            "message": "Receipt data extracted and stored successfully",
            "data": result,
            "confidence": result.get("confidence", 1)
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()