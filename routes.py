# server/routes.py
from flask import Blueprint, request, jsonify
from utils import  normalize_transaction
from db_config import get_connection
from utils import  callAzureOCR, receiptClassifier,redirectReceipt

import io


routes = Blueprint('routes', __name__)

@routes.route('/', methods=['POST'])
def home():
        return jsonify({
        "message": "APP IS LIVE",
        }), 200

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
        
        classifyRecepit = receiptClassifier(azure_json) 

        if classifyRecepit is 'unknown':
             return jsonify({"error": "Invalid OCR response"}), 500
        
        # print(classifyRecepit, 'CHECK PAYMENT MODE HERE')
        parser = redirectReceipt(azure_json,classifyRecepit)
        result = parser.parse()
        # print(result, 'res')
        # Normalize the transaction data for database insertion
        normalized_tx = normalize_transaction(result)
        print(normalized_tx)
        
        # Insert into database
        conn = get_connection()
        cur = conn.cursor()
        
        # # Add created_at timestamp
        # from datetime import datetime
        # normalized_tx['created_at'] = datetime.now()
        
        # cur.execute("""
        #     INSERT INTO transactions_live (
        #         date_of_transaction, receiver_name, receiver_bank,
        #         message, transaction_number, sent_from, utr,
        #         receiver_phone_number, amount, upi_method, confidence, created_at
        #     ) VALUES (
        #         %(date_of_transaction)s, %(receiver_name)s, %(receiver_bank)s,
        #         %(message)s, %(transaction_number)s, %(sent_from)s, %(utr)s,
        #         %(receiver_phone_number)s, %(amount)s, %(upi_method)s, %(confidence)s, %(created_at)s
        #     )
        # """, normalized_tx)
        
        # conn.commit()
        
        return jsonify({
            "message": "Receipt data extracted and stored successfully",
            "data": normalized_tx,
            "confidence": result.get("confidence", 1)
        }), 200

        # return jsonify({
        #     "message": "Receipt data extracted and stored successfully",
        #     "data": classifyRecepit,
        #     # "confidence": result.get("confidence", 1)
        # }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()



    