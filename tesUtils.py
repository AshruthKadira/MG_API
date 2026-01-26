
import re
from datetime import datetime
from typing import Dict, List, Optional

def clean_phonepe_transaction(ocr_data: List[Dict]) -> Dict:
    """
    Clean PhonePe transaction receipt data extracted from OCR.
    
    Args:
        ocr_data: List of dictionaries containing OCR text and coordinates
        
    Returns:
        Dictionary with cleaned transaction data ready for PostgreSQL insertion
    """
    
    # Extract all text values sorted by y-coordinate (top to bottom)
    texts = sorted(ocr_data, key=lambda x: x['y'])
    text_values = [item['text'] for item in texts]
    
    # Initialize result dictionary
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
    
    # Helper function to find text by pattern
    def find_text_by_pattern(pattern, text_list):
        for i, text in enumerate(text_list):
            if re.search(pattern, text, re.IGNORECASE):
                return i, text
        return None, None
    
    # Helper function to get next non-empty text
    def get_next_text(index, text_list, skip=0):
        for i in range(index + 1 + skip, len(text_list)):
            if text_list[i].strip():
                return text_list[i]
        return None
    
    # 1. Extract Status
    if 'Successful' in text_values:
        result['status'] = 'Successful'
    elif 'Failed' in text_values:
        result['status'] = 'Failed'
    
    # 2. Extract Date and Time
    date_pattern = r'(\d{1,2}:\d{2})\s*(am|pm).*?(\d{1,2})\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{4})'
    full_text = ' '.join(text_values)
    date_match = re.search(date_pattern, full_text, re.IGNORECASE)
    
    if date_match:
        time_str = f"{date_match.group(1)} {date_match.group(2)}"
        day = date_match.group(3)
        month = date_match.group(4)
        year = date_match.group(5)
        
        try:
            # Convert to standard date format
            date_str = f"{day} {month} {year} {time_str}"
            parsed_date = datetime.strptime(date_str, "%d %b %Y %I:%M %p")
            result['date_of_transaction'] = parsed_date.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    
    # 3. Extract Amount (look for currency symbols or amount patterns)
    amount_patterns = [
        r'[₹~%F-](\d{1,3}(?:,\d{3})*)',  # ₹1,000 or ~1,000 or %1,000 or F1,000
        r'(\d{1,3}(?:,\d{3})*)',  # Plain numbers like 1,000
    ]
    
    for text in text_values:
        for pattern in amount_patterns:
            match = re.search(pattern, text)
            if match and len(match.group(1)) >= 2:  # At least 2 digits
                amount_str = match.group(1).replace(',', '')
                try:
                    amount = float(amount_str)
                    if amount >= 10:  # Reasonable minimum amount
                        result['amount'] = amount
                        break
                except ValueError:
                    continue
        if result['amount']:
            break
    
    # 4. Extract Receiver Name (usually appears after "Paid to" and before UPI ID)
    paid_idx, _ = find_text_by_pattern(r'paid.*to', text_values)
    if paid_idx is not None:
        # Look for receiver name in the next few texts
        potential_names = []
        for i in range(paid_idx + 1, min(paid_idx + 6, len(text_values))):
            text = text_values[i].strip()
            # Skip if it looks like UPI ID, amount, or common words
            if not re.search(r'[@.\d%~₹F-]|^(to|and|the|of)$', text, re.IGNORECASE) and len(text) > 1:
                potential_names.append(text)
            if '@' in text or text.startswith('paytm') or text.startswith('gpay'):
                break
        
        if potential_names:
            result['reciever_name'] = ' '.join(potential_names[:3])  # Take first 3 parts max
    
    # 5. Extract UPI ID/Phone Number
    for text in text_values:
        # UPI ID patterns
        if '@' in text and any(domain in text.lower() for domain in ['ybl', 'paytm', 'okicici', 'axl', 'okb', 'hdfcbank']):
            result['upi_method'] = text.strip()
            break
        # Phone number pattern
        elif re.match(r'^[+]?\d{10,13}$', text.strip()):
            result['reciever_phone_number'] = text.strip()
    
    # 6. Extract Banking Name (usually after "Banking Na..." or near UPI ID)
    banking_idx, _ = find_text_by_pattern(r'banking.*na', text_values)
    if banking_idx is not None:
        # Get text after the colon
        for i in range(banking_idx + 1, min(banking_idx + 5, len(text_values))):
            if ':' in text_values[i-1] or text_values[i-1].endswith('...'):
                potential_banking = []
                for j in range(i, min(i + 3, len(text_values))):
                    text = text_values[j].strip()
                    if text and not text.startswith('@') and len(text) > 1:
                        potential_banking.append(text)
                    else:
                        break
                if potential_banking:
                    result['banking_name'] = ' '.join(potential_banking)
                break
    
    # 7. Extract Transaction ID
    for text in text_values:
        if text.startswith('T') and len(text) > 15 and text.isalnum():
            result['transaction_number'] = text
            break
    
    # 8. Extract UTR
    utr_idx, _ = find_text_by_pattern(r'utr', text_values)
    if utr_idx is not None:
        utr_text = get_next_text(utr_idx, text_values)
        if utr_text and re.match(r'^\d{10,15}$', utr_text):
            result['utr'] = utr_text
    
    # 9. Extract Message (look for payment details or message)
    message_idx, _ = find_text_by_pattern(r'message', text_values)
    if message_idx is not None:
        message_text = get_next_text(message_idx, text_values)
        if message_text and len(message_text) > 1:
            result['message'] = message_text
    
    # 10. Extract Sent From (look for account details)
    sent_from_pattern = r'XX+\d+'  # XXXXXXXXXXXI132 pattern
    for text in text_values:
        if re.search(sent_from_pattern, text):
            result['sent_from'] = text
            break
    
    # Clean up None values and empty strings
    for key, value in result.items():
        if value == '' or value == 'None':
            result[key] = None
    
    return result

# Example usage and test function
def test_cleaner():
    """Test the cleaner with sample data"""
    sample_data = [
        {'text': 'Transaction', 'x': 153, 'y': 25, 'w': 229, 'h': 33},
        {'text': 'Successful', 'x': 400, 'y': 25, 'w': 215, 'h': 33},
        {'text': '09:29', 'x': 153, 'y': 76, 'w': 87, 'h': 24},
        {'text': 'pm', 'x': 252, 'y': 82, 'w': 44, 'h': 25},
        {'text': '26', 'x': 357, 'y': 76, 'w': 36, 'h': 24},
        {'text': 'Aug', 'x': 405, 'y': 76, 'w': 58, 'h': 31},
        {'text': '2025', 'x': 477, 'y': 76, 'w': 75, 'h': 24},
        {'text': 'Paid', 'x': 52, 'y': 174, 'w': 82, 'h': 32},
        {'text': 'to', 'x': 146, 'y': 178, 'w': 38, 'h': 28},
        {'text': '1,100', 'x': 623, 'y': 239, 'w': 125, 'h': 40},
        {'text': 'T2508262129012644053905', 'x': 51, 'y': 818, 'w': 536, 'h': 30},
        {'text': 'XXXXXXXXXXXI132', 'x': 168, 'y': 959, 'w': 408, 'h': 32},
        {'text': '770546476094', 'x': 274, 'y': 1034, 'w': 295, 'h': 31}
    ]
    
    result = clean_phonepe_transaction(sample_data)
    print("Cleaned transaction data:")
    for key, value in result.items():
        print(f"{key}: {value}")

if __name__ == "__main__":
    test_cleaner()