# routes.py
from flask import Blueprint, request, jsonify
from utils import change_content, transform_values_and_keys, stringify_keys_but_keep_values, normalize_transaction
from db_config import get_connection
from transaction_processor import TransactionProcessor
import json

routes = Blueprint('routes', __name__)

# Initialize processor (set use_llm=True to enable LLM)
processor = TransactionProcessor(use_llm=True, strict_validation=False)

@routes.route('/', methods=['POST'])
def home():
    """Original text-based route"""
    data = request.get_json()
    if 'data' not in data:
        return {"error": "Missing 'data' key"}, 400

    array_str = data['data'].replace("\n", "")
    parsed_data = change_content(array_str)

    if isinstance(parsed_data, tuple) and "error" in parsed_data[0]:
        return parsed_data

    transformed = transform_values_and_keys(parsed_data)
    safe_to_insert = stringify_keys_but_keep_values(transformed)
    
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

@routes.route('/process-receipt', methods=['POST'])
def process_receipt():
    """
    New route: Process receipt with OCR + LLM cross-validation
    Accepts: raw image bytes
    Returns: validated transaction data
    """
    try:
        # Get image bytes
        image_bytes = request.data
        
        if not image_bytes:
            return jsonify({"error": "No image data provided"}), 400
        
        # Process with cross-validation
        validated_data, metadata = processor.process_receipt(image_bytes)
        
        # Determine action
        action = processor.get_processing_action(validated_data)
        
        # Prepare response
        response = {
            "action": action,
            "confidence": validated_data.get('confidence'),
            "transaction_data": validated_data,
            "metadata": {
                "extraction_methods": metadata['extraction_methods'],
                "needs_review": validated_data.get('needs_review', False),
            }
        }
        
        # If high confidence, insert to DB
        if action == 'insert':
            try:
                conn = get_connection()
                cur = conn.cursor()
                
                normalized_tx = normalize_transaction(validated_data)
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
                response["database_status"] = "inserted"
                
            except Exception as e:
                response["database_status"] = "failed"
                response["database_error"] = str(e)
            finally:
                if 'cur' in locals():
                    cur.close()
                if 'conn' in locals():
                    conn.close()
        
        # Add validation report if available
        if metadata.get('validation_report'):
            response["validation_report"] = metadata['validation_report']
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "action": "reject"
        }), 500

@routes.route('/get-images', methods=['POST'])
def image_route():
    """Original OCR-only route (kept for backward compatibility)"""
    try:
        image_bytes = request.data
        
        # Use OCR extractor directly
        from ocr_extractor import OCRExtractor
        ocr = OCRExtractor(languages=['en', 'hi'])
        cleaned_data = ocr.process_image(image_bytes)
        
        return jsonify({"ocr_results": cleaned_data}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500