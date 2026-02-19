# server/routes.py
import io
import os
import re
import json
import base64
import traceback

import ollama
from flask import Blueprint, request, jsonify
from PIL import Image
from dotenv import load_dotenv
from paddleocr import PaddleOCR
from db_config import get_connection
from utils import normalize_transaction
from utils import preprocess_receipt_from_bytes, parse_receipt_to_json, preprocess_receipt_for_rupee
load_dotenv()

routes = Blueprint('routes', __name__)

ocr = PaddleOCR(lang='en')  # Initialize once globally

import re

def normalize_rupee(text_list):
    corrected = []

    for text in text_list:
        # Replace E300 or F300 with ₹300
        text = re.sub(r'\b[E|F](\d+)\b', r'₹\1', text)

        # Replace Rs 300 with ₹300
        text = re.sub(r'Rs\.?\s?(\d+)', r'₹\1', text, flags=re.IGNORECASE)

        corrected.append(text)

    return corrected


# ---------------------------------------------------------------------------
# Ollama config
# Model: qwen2-vl:7b
#   • Best open-source vision-language model for receipt extraction
#   • Natively handles ₹, Indian names, UPI IDs, masked account numbers
#   • Runs 100% locally — your images never leave your machine
#   • Ollama must be running: `ollama serve`
#   • Pull the model once: `ollama pull qwen2-vl:7b`
# ---------------------------------------------------------------------------
_MODEL       = "qwen2-vl:7b"
_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# Initialise the Ollama client once at startup — not per request.
# Passing host= lets us point at a remote Ollama instance (Option 2 / 3)
# without changing any other code — just update OLLAMA_HOST in .env.
_ollama_client = ollama.Client(host=_OLLAMA_HOST)
print(f"Ollama client initialised → {_OLLAMA_HOST}")


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are a data extraction assistant specialised in Indian UPI payment receipts.
Extract the transaction fields from the receipt image and return ONLY a valid \
JSON object — no explanation, no markdown, no code fences.

JSON schema (use null for any field not found):
{
  "status":                 string,   // "Successful" | "Failed" | "Pending"
  "date_of_transaction":   string,   // ISO format: "YYYY-MM-DD HH:MM:SS" (24-hour)
  "reciever_name":         string,   // Full name of the recipient
  "banking_name":          string,   // UPI app or bank used e.g. "Paytm", "HDFC Bank"
  "message":               string,   // Payment note / message if present, else null
  "transaction_number":    string,   // Full transaction ID starting with T
  "sent_from":             string,   // Masked account number e.g. "XXXX9562"
  "utr":                   string,   // UTR number (numeric string)
  "reciever_phone_number": string,   // 10-digit mobile number or null
  "amount":                number,   // Transaction amount as plain number e.g. 300
  "upi_method":            string,   // UPI app name e.g. "PhonePe", "Paytm", "GPay"
  "upi_id":                string    // UPI VPA e.g. "XXXXXX3997@ptyes"
}"""

_USER_PROMPT = (
    "Extract all transaction fields from this PhonePe receipt "
    "and return them as a JSON object matching the schema. "
    "Return ONLY the raw JSON — no markdown, no explanation."
)


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _extract_image_bytes() -> bytes:
    """Accept images as multipart/form-data or raw binary body."""
    content_type = request.content_type or ""

    if "multipart/form-data" in content_type:
        if not request.files:
            raise ValueError(
                "multipart/form-data received but no file attached. "
                "In Postman: Body → form-data → key type = File."
            )
        fs = next(iter(request.files.values()))
        raw = fs.read()
        if not raw:
            raise ValueError(f"Uploaded file '{fs.filename}' is empty.")
        return raw

    if request.data:
        return request.data

    raise ValueError(
        "No image found. Send as multipart/form-data (file field) "
        "or raw binary body (Content-Type: application/octet-stream)."
    )


def _to_base64_jpeg(raw_bytes: bytes) -> str:
    """
    Normalise any image format to a JPEG base64 string.
    Ollama's vision API accepts images as plain base64 strings (no data-URI prefix).
    JPEG is used because it's smallest for photos and screenshots.
    """
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

def extract_with_llm(raw_bytes: bytes) -> dict:
    """
    Send the receipt image to Qwen2-VL running in Ollama and return the
    parsed transaction dict.

    Ollama's chat API accepts images as a list of base64 strings alongside
    the message content. The system prompt enforces the JSON schema; the
    user message provides the image + extraction instruction.

    Flow:
      raw bytes → base64 JPEG → ollama.chat() → raw text → strip fences
                → json.loads() → dict
    """
    b64 = _to_base64_jpeg(raw_bytes)

    response = _ollama_client.chat(
        model=_MODEL,
        messages=[
            {
                "role": "system",
                "content": _SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": _USER_PROMPT,
                "images": [b64],   # list of base64 strings — Ollama's vision format
            },
        ],
        options={
            "temperature": 0,      # deterministic output — critical for structured data
            "num_predict": 512,    # max tokens to generate
        },
    )

    raw_content = response["message"]["content"].strip()
    print("=== LLM RAW RESPONSE ===\n", raw_content)

    # Strip markdown code fences the model may add despite instructions
    # e.g.  ```json\n{...}\n```  →  {...}
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_content, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Model returned invalid JSON: {e}\n"
            f"Raw output was:\n{raw_content}"
        )


# ---------------------------------------------------------------------------
# DB insert
# ---------------------------------------------------------------------------

def _insert_transaction(tx: dict) -> dict:
    """
    Normalise the LLM output and insert into PostgreSQL.
    Returns the normalised dict so it can be included in the API response.
    """
    normalized = normalize_transaction(tx)
    print("=== NORMALIZED ===\n", json.dumps(normalized, indent=2, default=str))

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO transactions (
                status, date_of_transaction, reciever_name, banking_name,
                message, transaction_number, sent_from, utr,
                reciever_phone_number, amount, upi_method
            ) VALUES (
                %(status)s, %(date_of_transaction)s, %(reciever_name)s, %(banking_name)s,
                %(message)s, %(transaction_number)s, %(sent_from)s, %(utr)s,
                %(reciever_phone_number)s, %(amount)s, %(upi_method)s
            )
            """,
            normalized,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    return normalized


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

# @routes.route('/get-images', methods=['POST'])
# def image_route():
#     """
#     POST /get-images
#     Body: receipt image (multipart/form-data file field, or raw binary body)

#     Pipeline:
#       1. Extract image bytes from request
#       2. Convert to base64 JPEG
#       3. Send to Qwen2-VL in Ollama → structured JSON
#       4. Normalise field types / formats
#       5. Insert into PostgreSQL
#       6. Return stored data
#     """
#     try:
#         raw_bytes = _extract_image_bytes()
#         print(f"Received {len(raw_bytes):,} bytes")

#         extracted = extract_with_llm(raw_bytes)
#         print("=== EXTRACTED ===\n", json.dumps(extracted, indent=2, default=str))

#         stored = _insert_transaction(extracted)

#         return jsonify({
#             "message": "Transaction extracted and stored successfully",
#             "data": stored,
#         }), 200

#     except ValueError as e:
#         return jsonify({"error": str(e)}), 400

#     except Exception as e:
#         traceback.print_exc()
#         return jsonify({"error": str(e)}), 500



@routes.route('/get-images', methods=['POST'])
def image_route():
    try:
        raw_bytes = _extract_image_bytes()

        processed_img = preprocess_receipt_for_rupee(raw_bytes)

        # ✅ Use predict() instead of ocr()
        result = ocr.predict(processed_img)
        print('ocr process done')
        texts = result[0]["rec_texts"]
        scores = result[0]["rec_scores"]
        print(result[0]["rec_texts"], 'OCR RESULTS')
        filtered = [
            t for t, s in zip(texts, scores)
            if s > 0.6
        ]

        normalized = normalize_rupee(filtered)

        structured = parse_receipt_to_json(normalized)
        print(structured, 'STRUCTURED OUTPUT')
        return jsonify({
            "message": "OCR completed successfully",
            "text": structured
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

