# test_processor.py
"""
Test script for transaction processor
"""
from transaction_processor import TransactionProcessor
from PIL import Image
import io
import sys

def test_with_sample_image(image_path: str):
    """
    Test processor with a sample receipt image
    """
    print(f"Testing with image: {image_path}")
    print("="*80)
    
    # Read image
    with open(image_path, 'rb') as f:
        image_bytes = f.read()
    
    # Initialize processor
    # Set use_llm=False to test OCR only
    # Set use_llm=True to test with cross-validation
    processor = TransactionProcessor(use_llm=True, strict_validation=False)
    
    # Process
    validated_data, metadata = processor.process_receipt(image_bytes)
    
    # Display results
    print("\n" + "="*80)
    print("FINAL RESULTS")
    print("="*80)
    
    print(f"\nAction: {processor.get_processing_action(validated_data)}")
    print(f"Confidence: {validated_data.get('confidence', 0):.1f}%")
    print(f"Needs Review: {validated_data.get('needs_review', False)}")
    
    print("\nValidated Transaction Data:")
    for key, value in validated_data.items():
        if key not in ['conflicts', 'resolutions', 'extraction_method', 'ocr_confidence', 'llm_model']:
            print(f"  {key}: {value}")
    
    # Show comparison if LLM was used
    if metadata.get('llm_data'):
        print("\n" + "="*80)
        print("COMPARISON (OCR vs LLM)")
        print("="*80)
        
        fields = ['amount', 'transaction_number', 'date_of_transaction', 'reciever_name', 'status']
        for field in fields:
            ocr_val = metadata['ocr_data'].get(field)
            llm_val = metadata['llm_data'].get(field)
            validated_val = validated_data.get(field)
            
            match = "✓" if ocr_val == llm_val else "✗"
            print(f"\n{field}:")
            print(f"  OCR: {ocr_val}")
            print(f"  LLM: {llm_val}")
            print(f"  Final: {validated_val} {match}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_processor.py <image_path>")
        print("Example: python test_processor.py sample_receipt.jpg")
        sys.exit(1)
    
    test_with_sample_image(sys.argv[1])