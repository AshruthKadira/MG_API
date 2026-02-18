# transaction_processor.py
from ocr_extractor import OCRExtractor
from llm_extractor import LLMExtractor
from cross_validator import CrossValidator, ConflictError
from typing import Dict, Tuple
import json

class TransactionProcessor:
    """
    Complete transaction processing pipeline with cross-validation
    """
    
    def __init__(self, use_llm=True, strict_validation=False):
        """
        Initialize processors
        use_llm: If False, only use OCR (for cost savings)
        strict_validation: If True, raise errors on conflicts
        """
        self.ocr_extractor = OCRExtractor(languages=['en', 'hi'])
        self.llm_extractor = LLMExtractor() if use_llm else None
        self.validator = CrossValidator(strict_mode=strict_validation)
        self.use_llm = use_llm
    
    def process_receipt(self, image_bytes: bytes) -> Tuple[Dict, Dict]:
        """
        Process receipt image with both OCR and LLM (if enabled)
        Returns: (validated_data, metadata)
        """
        # Step 1: Extract with OCR
        print("Extracting with OCR...")
        ocr_data = self.ocr_extractor.process_image(image_bytes)
        print(f"OCR Result: {json.dumps(ocr_data, indent=2)}")
        
        # Step 2: Extract with LLM (if enabled)
        llm_data = None
        if self.use_llm:
            print("\nExtracting with LLM...")
            try:
                llm_data = self.llm_extractor.process_image(image_bytes)
                print(f"LLM Result: {json.dumps(llm_data, indent=2)}")
            except Exception as e:
                print(f"LLM extraction failed: {e}")
                print("Falling back to OCR only")
                llm_data = None
        
        # Step 3: Cross-validate (if both available)
        if llm_data:
            print("\nCross-validating...")
            validated_data = self.validator.validate(ocr_data, llm_data)
            validation_report = self.validator.get_validation_report()
            
            print(f"\nValidation Report:")
            print(f"Confidence: {validated_data['confidence']:.1f}%")
            print(f"Conflicts: {validation_report['total_conflicts']}")
            if validation_report['conflicts']:
                print("Conflict details:")
                for conflict in validation_report['conflicts']:
                    print(f"  - {conflict}")
            if validation_report['resolutions']:
                print("Resolutions:")
                for resolution in validation_report['resolutions']:
                    print(f"  - {resolution}")
            
            metadata = {
                'extraction_methods': ['OCR', 'LLM'],
                'validation_report': validation_report,
                'ocr_data': ocr_data,
                'llm_data': llm_data
            }
        else:
            # OCR only
            validated_data = ocr_data
            validated_data['confidence'] = ocr_data.get('ocr_confidence', 0) * 100
            validated_data['needs_review'] = validated_data['confidence'] < 70
            
            metadata = {
                'extraction_methods': ['OCR'],
                'validation_report': None,
                'ocr_data': ocr_data,
                'llm_data': None
            }
        
        print(f"\nFinal Validated Data: {json.dumps(validated_data, indent=2)}")
        
        return validated_data, metadata
    
    def should_insert_to_db(self, validated_data: Dict) -> bool:
        """
        Determine if data is ready for DB insertion
        """
        # Check confidence
        if validated_data.get('confidence', 0) < 80:
            return False
        
        # Check critical fields
        critical_fields = ['amount', 'status', 'date_of_transaction']
        for field in critical_fields:
            if validated_data.get(field) is None:
                return False
        
        # Check if needs review
        if validated_data.get('needs_review', False):
            return False
        
        return True
    
    def get_processing_action(self, validated_data: Dict) -> str:
        """
        Determine what action to take with validated data
        Returns: 'insert', 'review', or 'reject'
        """
        confidence = validated_data.get('confidence', 0)
        
        if confidence >= 95 and not validated_data.get('needs_review'):
            return 'insert'
        elif confidence >= 70:
            return 'review'
        else:
            return 'reject'