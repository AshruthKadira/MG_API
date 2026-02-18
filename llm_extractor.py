# llm_extractor.py
import requests
import json
import base64
from typing import Dict
from PIL import Image
import io

class LLMExtractor:
    """
    Open-source LLM pipeline using Ollama with vision models
    Requires: Ollama installed locally with llama3.2-vision or llava model
    
    Installation:
    1. Install Ollama: curl -fsSL https://ollama.ai/install.sh | sh
    2. Pull vision model: ollama pull llama3.2-vision
    """
    
    def __init__(self, model_name='llama3.2-vision', base_url='http://localhost:11434'):
        """
        Initialize LLM extractor
        model_name: 'llama3.2-vision' or 'llava' or 'llava:13b'
        base_url: Ollama API endpoint
        """
        self.model_name = model_name
        self.base_url = base_url
        self.api_endpoint = f"{base_url}/api/generate"
    
    def create_extraction_prompt(self) -> str:
        """
        Create structured prompt for transaction extraction
        """
        prompt = """You are a transaction receipt analyzer. Extract the following information from this PhonePe/GooglePay/Paytm receipt image.

Output ONLY a valid JSON object with these exact fields (use null if field not found):

{
  "status": "transaction status (Successful/Failed/Pending)",
  "date_of_transaction": "date in YYYY-MM-DD HH:MM:SS format",
  "reciever_name": "receiver's full name",
  "banking_name": "bank name",
  "message": "transaction message/remark",
  "transaction_number": "transaction ID (starts with T)",
  "sent_from": "sender account number (masked with X)",
  "utr": "UTR number",
  "reciever_phone_number": "receiver's phone number (10 digits)",
  "amount": "transaction amount as number (without currency symbol)",
  "upi_method": "UPI ID (email@provider format)"
}

Important rules:
1. Extract amount WITHOUT rupee symbol (₹), just the number
2. Format date as YYYY-MM-DD HH:MM:SS (convert from any format shown)
3. Extract full transaction number starting with T
4. For phone numbers, extract only 10 digits
5. UPI method should be the email@provider format
6. If field is not visible, use null

Output ONLY the JSON, no explanations."""
        
        return prompt
    
    def encode_image(self, image_bytes: bytes) -> str:
        """
        Encode image to base64 for API
        """
        return base64.b64encode(image_bytes).decode('utf-8')
    
    def extract_from_image(self, image_bytes: bytes) -> Dict:
        """
        Extract transaction data using LLM vision model
        """
        try:
            # Encode image
            image_b64 = self.encode_image(image_bytes)
            
            # Create prompt
            prompt = self.create_extraction_prompt()
            
            # Prepare request
            payload = {
                "model": self.model_name,
                "prompt": prompt,
                "images": [image_b64],
                "stream": False,
                "format": "json"  # Request JSON output
            }
            
            # Make API call
            response = requests.post(self.api_endpoint, json=payload, timeout=120)
            response.raise_for_status()
            
            # Parse response
            result = response.json()
            generated_text = result.get('response', '')
            
            # Extract JSON from response
            transaction_data = self._parse_json_response(generated_text)
            
            # Add metadata
            transaction_data['extraction_method'] = 'LLM'
            transaction_data['llm_model'] = self.model_name
            
            return transaction_data
            
        except requests.exceptions.ConnectionError:
            raise Exception(
                "Cannot connect to Ollama. Please ensure:\n"
                "1. Ollama is installed: curl -fsSL https://ollama.ai/install.sh | sh\n"
                "2. Ollama is running: ollama serve\n"
                "3. Vision model is pulled: ollama pull llama3.2-vision"
            )
        except Exception as e:
            print(f"LLM extraction error: {e}")
            return self._get_empty_result()
    
    def _parse_json_response(self, text: str) -> Dict:
        """
        Parse JSON from LLM response (might have extra text)
        """
        try:
            # Try direct JSON parse
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON from text
            import re
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(0))
                except:
                    pass
            
            print(f"Failed to parse JSON from LLM response: {text[:200]}")
            return self._get_empty_result()
    
    def _get_empty_result(self) -> Dict:
        """
        Return empty transaction structure
        """
        return {
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
    
    def process_image(self, image_bytes: bytes) -> Dict:
        """
        Complete LLM processing pipeline
        """
        return self.extract_from_image(image_bytes)