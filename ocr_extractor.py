# ocr_extractor.py
import easyocr
import numpy as np
from PIL import Image
import io
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

class OCRExtractor:
    """
    Open-source OCR pipeline using EasyOCR (better for rupee symbols)
    """
    
    def __init__(self, languages=['en', 'hi']):
        """
        Initialize EasyOCR reader
        languages: ['en', 'hi'] for English and Hindi (rupee symbol)
        """
        self.reader = easyocr.Reader(languages, gpu=False)  # Set gpu=True if available
    
    def extract_text_from_image(self, image_bytes: bytes) -> List[Dict]:
        """
        Extract text with bounding boxes from image
        Returns: List of dicts with text, bbox, and confidence
        """
        # Convert bytes to numpy array
        image = Image.open(io.BytesIO(image_bytes))
        image_np = np.array(image)
        
        # Perform OCR
        results = self.reader.readtext(image_np, detail=1)
        
        # Format results
        extracted_data = []
        for bbox, text, confidence in results:
            # bbox is [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
            x_coords = [point[0] for point in bbox]
            y_coords = [point[1] for point in bbox]
            
            extracted_data.append({
                'text': text,
                'x': int(min(x_coords)),
                'y': int(min(y_coords)),
                'w': int(max(x_coords) - min(x_coords)),
                'h': int(max(y_coords) - min(y_coords)),
                'confidence': confidence
            })
        
        return extracted_data
    
    def parse_transaction_data(self, ocr_results: List[Dict]) -> Dict:
        """
        Parse OCR results into structured transaction data
        """
        # Sort by y-coordinate (top to bottom)
        texts = sorted(ocr_results, key=lambda x: x['y'])
        text_values = [item['text'] for item in texts]
        full_text = ' '.join(text_values)
        
        result = {
            'status': None,
            'date_of_transaction': None,
            'reciever_name': None,
            'banking_name': None,
            'message': None,
            'transaction_number': None,
            'sent_from': None,
            'utr': None,
            'reciever_phone_number': None,
            'amount': None,
            'upi_method': None
        }
        
        # 1. Extract Status
        for text in text_values:
            if 'success' in text.lower():
                result['status'] = 'Successful'
                break
            elif 'fail' in text.lower():
                result['status'] = 'Failed'
                break
        
        # 2. Extract Date and Time
        date_patterns = [
            r'(\d{1,2}:\d{2})\s*(am|pm|AM|PM).*?(\d{1,2})\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{4})',
            r'(\d{1,2})\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{4}).*?(\d{1,2}:\d{2})\s*(am|pm|AM|PM)',
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                try:
                    if 'am' in match.group(0).lower() or 'pm' in match.group(0).lower():
                        # Extract components based on pattern
                        groups = match.groups()
                        if len(groups) >= 5:
                            time_str = groups[0]
                            am_pm = groups[1]
                            day = groups[2]
                            month = groups[3]
                            year = groups[4]
                            
                            date_str = f"{day} {month} {year} {time_str} {am_pm}"
                            parsed_date = datetime.strptime(date_str, "%d %b %Y %I:%M %p")
                            result['date_of_transaction'] = parsed_date.strftime("%Y-%m-%d %H:%M:%S")
                            break
                except Exception as e:
                    print(f"Date parsing error: {e}")
                    continue
        
        # 3. Extract Amount (with rupee symbol detection)
        # EasyOCR better detects ₹ symbol
        amount_patterns = [
            r'[₹Rs.\s]*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)',  # ₹1,100 or Rs. 1,100
            r'(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*(?:₹|Rs)',  # 1,100 ₹
        ]
        
        amounts_found = []
        for text in text_values:
            for pattern in amount_patterns:
                matches = re.findall(pattern, text)
                for match in matches:
                    amount_str = match.replace(',', '').replace(' ', '')
                    try:
                        amount = float(amount_str)
                        if 10 <= amount <= 10000000:  # Reasonable range
                            amounts_found.append(amount)
                    except ValueError:
                        continue
        
        # Take the largest amount found (usually the transaction amount)
        if amounts_found:
            result['amount'] = max(amounts_found)
        
        # 4. Extract Receiver Name
        paid_to_idx = None
        for i, text in enumerate(text_values):
            if 'paid' in text.lower() and 'to' in text.lower():
                paid_to_idx = i
                break
        
        if paid_to_idx is not None:
            # Get next few texts as potential name
            potential_names = []
            for i in range(paid_to_idx + 1, min(paid_to_idx + 5, len(text_values))):
                text = text_values[i].strip()
                # Skip UPI IDs, phone numbers, amounts
                if not re.search(r'[@\d₹,.]|^(to|and|the)$', text, re.IGNORECASE) and len(text) > 1:
                    potential_names.append(text)
                if '@' in text or 'paytm' in text.lower():
                    break
            
            if potential_names:
                result['reciever_name'] = ' '.join(potential_names[:2])
        
        # 5. Extract UPI ID
        for text in text_values:
            if '@' in text:
                # Check for common UPI domains
                if any(domain in text.lower() for domain in ['ybl', 'paytm', 'okicici', 'axl', 'okb', 'hdfcbank', 'gpay', 'phonepe']):
                    result['upi_method'] = text.strip()
                    break
        
        # 6. Extract Phone Number
        for text in text_values:
            # Match 10-digit phone numbers
            phone_match = re.search(r'\b(\d{10})\b', text)
            if phone_match:
                result['reciever_phone_number'] = phone_match.group(1)
                break
        
        # 7. Extract Transaction Number
        for text in text_values:
            if text.startswith('T') and len(text) > 15:
                if re.match(r'^T\d+', text):
                    result['transaction_number'] = text
                    break
        
        # 8. Extract UTR
        for i, text in enumerate(text_values):
            if 'utr' in text.lower():
                # Check next text
                if i + 1 < len(text_values):
                    utr_text = text_values[i + 1]
                    if re.match(r'^\d{10,15}$', utr_text):
                        result['utr'] = utr_text
                        break
        
        # 9. Extract Banking Name
        for i, text in enumerate(text_values):
            if 'bank' in text.lower():
                # Get next 1-2 texts as bank name
                bank_parts = []
                for j in range(i, min(i + 3, len(text_values))):
                    if text_values[j] and not text_values[j].startswith('@'):
                        bank_parts.append(text_values[j])
                if bank_parts:
                    result['banking_name'] = ' '.join(bank_parts)
                    break
        
        # 10. Extract Sent From (account number pattern)
        for text in text_values:
            if re.search(r'X+\d+', text):
                result['sent_from'] = text
                break
        
        # 11. Extract Message
        for i, text in enumerate(text_values):
            if 'message' in text.lower() or 'remark' in text.lower():
                if i + 1 < len(text_values):
                    result['message'] = text_values[i + 1]
                    break
        
        return result
    
    def process_image(self, image_bytes: bytes) -> Dict:
        """
        Complete OCR processing pipeline
        """
        ocr_results = self.extract_text_from_image(image_bytes)
        transaction_data = self.parse_transaction_data(ocr_results)
        
        # Add metadata
        transaction_data['extraction_method'] = 'OCR'
        transaction_data['ocr_confidence'] = np.mean([r['confidence'] for r in ocr_results]) if ocr_results else 0
        
        return transaction_data