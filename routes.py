# server/routes.py
from flask import Blueprint, request, jsonify
from utils import change_content, transform_values_and_keys, stringify_keys_but_keep_values, normalize_transaction
from tesUtils import clean_phonepe_transaction
from db_config import get_connection
import pytesseract
from PIL import Image
import openai
import io
import json

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
                    status, date_of_transaction, reciever_name, banking_name,
                    message, transaction_number, sent_from, utr,
                    reciever_phone_number, amount, upi_method
                ) VALUES (
                    %(status)s, %(date_of_transaction)s, %(reciever_name)s, %(banking_name)s,
                    %(message)s, %(transaction_number)s, %(sent_from)s, %(utr)s,
                    %(reciever_phone_number)s, %(amount)s, %(upi_method)s
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

@routes.route('/get-images', methods =['POST'])
def image_route():
    print("Headers:", request.headers)
    print("Form Data:", request.form)
    try:
        # Get raw bytes
        image_bytes = request.data  

        # Convert to PIL Image
        image = Image.open(io.BytesIO(image_bytes))

        custom_config = r'-c tessedit_char_whitelist=0123456789₹., --psm 6'

        # Extract text with dimensions
        data = pytesseract.image_to_data(
            image,
            output_type=pytesseract.Output.DICT
        )

        results = []
        for i in range(len(data['text'])):
            if data['text'][i].strip():  # ignore empty text
                results.append({
                    "text": data['text'][i],
                    "x": data['left'][i],
                    "y": data['top'][i],
                    "w": data['width'][i],
                    "h": data['height'][i]
                })
        cleaned_data = clean_phonepe_transaction(results)
        print(cleaned_data, 'OCR RESULTS')
        return {"ocr_results": cleaned_data}

    except Exception as e:
        return {"error": str(e)}