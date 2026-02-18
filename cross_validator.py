# cross_validator.py
import difflib
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from fuzzywuzzy import fuzz
import re

class ConflictError(Exception):
    """Raised when cross-validation detects unresolvable conflicts"""
    pass

class CrossValidator:
    """
    Cross-validates transaction data from OCR and LLM sources
    """
    
    CRITICAL_FIELDS = ['amount', 'transaction_number', 'date_of_transaction']
    IMPORTANT_FIELDS = ['reciever_name', 'status', 'upi_method']
    OPTIONAL_FIELDS = ['utr', 'banking_name', 'message', 'sent_from', 'reciever_phone_number']
    
    def __init__(self, strict_mode=False):
        """
        strict_mode: If True, raise errors on conflicts. If False, try to resolve.
        """
        self.strict_mode = strict_mode
        self.conflicts = []
        self.resolutions = []
    
    def validate(self, ocr_data: Dict, llm_data: Dict) -> Dict:
        """
        Main validation function
        Returns: validated transaction data
        """
        self.conflicts = []
        self.resolutions = []
        
        validated = {}
        
        # Validate each field
        all_fields = self.CRITICAL_FIELDS + self.IMPORTANT_FIELDS + self.OPTIONAL_FIELDS
        
        for field in all_fields:
            try:
                validated[field] = self._validate_field(
                    field,
                    ocr_data.get(field),
                    llm_data.get(field)
                )
            except ConflictError as e:
                self.conflicts.append(str(e))
                validated[field] = None
        
        # Calculate confidence
        confidence = self._calculate_confidence(validated, ocr_data, llm_data)
        validated['confidence'] = confidence
        validated['conflicts'] = self.conflicts
        validated['resolutions'] = self.resolutions
        validated['needs_review'] = confidence < 80 or len(self.conflicts) > 0
        
        return validated
    
    def _validate_field(self, field: str, ocr_value, llm_value):
        """
        Validate a single field
        """
        # Both None
        if ocr_value is None and llm_value is None:
            return None
        
        # One is None
        if ocr_value is None:
            self.resolutions.append(f"{field}: OCR missed, using LLM value")
            return llm_value
        
        if llm_value is None:
            self.resolutions.append(f"{field}: LLM missed, using OCR value")
            return ocr_value
        
        # Both have values - check if they match
        if self._values_match(field, ocr_value, llm_value):
            return ocr_value  # They match, use either
        
        # Conflict - try to resolve
        return self._resolve_conflict(field, ocr_value, llm_value)
    
    def _values_match(self, field: str, val1, val2) -> bool:
        """
        Check if two values match (with field-specific logic)
        """
        # Convert to strings for comparison
        str1 = str(val1).strip().lower()
        str2 = str(val2).strip().lower()
        
        # Exact match
        if str1 == str2:
            return True
        
        # Amount comparison (with tolerance)
        if field == 'amount':
            try:
                amt1 = float(str(val1).replace(',', ''))
                amt2 = float(str(val2).replace(',', ''))
                # Allow 1% tolerance for rounding
                return abs(amt1 - amt2) / max(amt1, amt2, 1) < 0.01
            except:
                return False
        
        # Date comparison
        if field == 'date_of_transaction':
            try:
                date1 = self._parse_date(val1)
                date2 = self._parse_date(val2)
                if date1 and date2:
                    # Allow 1 minute tolerance
                    return abs((date1 - date2).total_seconds()) < 60
            except:
                pass
        
        # Name comparison (fuzzy)
        if field == 'reciever_name':
            similarity = fuzz.ratio(str1, str2)
            return similarity > 85
        
        return False
    
    def _resolve_conflict(self, field: str, ocr_value, llm_value):
        """
        Resolve conflicts between OCR and LLM
        """
        conflict_msg = f"{field}: OCR='{ocr_value}', LLM='{llm_value}'"
        
        # Field-specific resolution strategies
        if field == 'amount':
            return self._resolve_amount(ocr_value, llm_value, conflict_msg)
        
        elif field == 'transaction_number':
            return self._resolve_transaction_id(ocr_value, llm_value, conflict_msg)
        
        elif field == 'date_of_transaction':
            return self._resolve_date(ocr_value, llm_value, conflict_msg)
        
        elif field == 'reciever_name':
            return self._resolve_name(ocr_value, llm_value, conflict_msg)
        
        elif field == 'reciever_phone_number':
            return self._resolve_phone(ocr_value, llm_value, conflict_msg)
        
        else:
            # For other fields, prefer LLM (better context understanding)
            self.resolutions.append(f"{conflict_msg} -> Using LLM")
            return llm_value
    
    def _resolve_amount(self, ocr_amt, llm_amt, conflict_msg):
        """
        Resolve amount conflicts - CRITICAL FIELD
        """
        try:
            amt1 = float(str(ocr_amt).replace(',', '').replace('₹', '').strip())
            amt2 = float(str(llm_amt).replace(',', '').replace('₹', '').strip())
            
            # If very close (within 1%), use higher precision
            if abs(amt1 - amt2) / max(amt1, amt2) < 0.01:
                self.resolutions.append(f"{conflict_msg} -> Values very close, using OCR")
                return amt1
            
            # Significant difference - flag for review
            if self.strict_mode:
                raise ConflictError(f"{conflict_msg} -> CRITICAL: Amount mismatch")
            else:
                self.conflicts.append(f"{conflict_msg} -> CRITICAL: Needs review")
                # Return the more reasonable value (or None)
                if 10 <= amt1 <= 10000000:
                    return amt1
                elif 10 <= amt2 <= 10000000:
                    return amt2
                return None
                
        except Exception as e:
            self.conflicts.append(f"{conflict_msg} -> ERROR: {str(e)}")
            return None
    
    def _resolve_transaction_id(self, ocr_id, llm_id, conflict_msg):
        """
        Resolve transaction ID conflicts
        """
        str1 = str(ocr_id).strip().upper()
        str2 = str(llm_id).strip().upper()
        
        # Check if one is substring of other (truncation)
        if str1 in str2:
            self.resolutions.append(f"{conflict_msg} -> OCR truncated, using LLM")
            return llm_id
        
        if str2 in str1:
            self.resolutions.append(f"{conflict_msg} -> LLM truncated, using OCR")
            return ocr_id
        
        # Calculate similarity
        similarity = difflib.SequenceMatcher(None, str1, str2).ratio()
        
        if similarity > 0.90:
            # Likely OCR error (character confusion)
            self.resolutions.append(f"{conflict_msg} -> High similarity ({similarity:.2f}), using LLM")
            return llm_id
        
        # Low similarity - flag
        self.conflicts.append(f"{conflict_msg} -> Low similarity ({similarity:.2f})")
        return None
    
    def _resolve_date(self, ocr_date, llm_date, conflict_msg):
        """
        Resolve date conflicts
        """
        try:
            date1 = self._parse_date(ocr_date)
            date2 = self._parse_date(llm_date)
            
            if date1 and date2:
                diff_seconds = abs((date1 - date2).total_seconds())
                
                if diff_seconds < 60:
                    # Within 1 minute - same transaction
                    self.resolutions.append(f"{conflict_msg} -> Within tolerance, using OCR")
                    return date1.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    # Different times
                    self.conflicts.append(f"{conflict_msg} -> Time difference: {diff_seconds}s")
                    # Prefer OCR for exact timestamps
                    return date1.strftime("%Y-%m-%d %H:%M:%S")
            
            # Can't parse one or both
            if date1:
                return date1.strftime("%Y-%m-%d %H:%M:%S")
            if date2:
                return date2.strftime("%Y-%m-%d %H:%M:%S")
            
            return None
            
        except Exception as e:
            self.conflicts.append(f"{conflict_msg} -> Parse error: {str(e)}")
            return None
    
    def _resolve_name(self, ocr_name, llm_name, conflict_msg):
        """
        Resolve name conflicts
        """
        similarity = fuzz.ratio(str(ocr_name).lower(), str(llm_name).lower())
        
        if similarity > 70:
            # Use longer version (likely more complete)
            chosen = llm_name if len(str(llm_name)) > len(str(ocr_name)) else ocr_name
            self.resolutions.append(
                f"{conflict_msg} -> Similarity {similarity}%, using longer: '{chosen}'"
            )
            return chosen
        
        # Low similarity - might be different people
        self.conflicts.append(f"{conflict_msg} -> Low similarity ({similarity}%)")
        # Prefer LLM (better at names)
        return llm_name
    
    def _resolve_phone(self, ocr_phone, llm_phone, conflict_msg):
        """
        Resolve phone number conflicts
        """
        # Extract digits only
        ocr_digits = re.sub(r'\D', '', str(ocr_phone))
        llm_digits = re.sub(r'\D', '', str(llm_phone))
        
        # Take last 10 digits
        ocr_last10 = ocr_digits[-10:] if len(ocr_digits) >= 10 else ocr_digits
        llm_last10 = llm_digits[-10:] if len(llm_digits) >= 10 else llm_digits
        
        if ocr_last10 == llm_last10:
            self.resolutions.append(f"{conflict_msg} -> Last 10 digits match")
            return ocr_last10
        
        # Different numbers
        self.conflicts.append(f"{conflict_msg} -> Different phone numbers")
        # Prefer OCR for numbers
        return ocr_phone if len(ocr_digits) == 10 else llm_phone
    
    def _parse_date(self, date_str) -> Optional[datetime]:
        """
        Parse date string into datetime object
        """
        if not date_str:
            return None
        
        date_formats = [
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d %b %Y %I:%M %p",
            "%Y-%m-%dT%H:%M:%S",
        ]
        
        for fmt in date_formats:
            try:
                return datetime.strptime(str(date_str).strip(), fmt)
            except:
                continue
        
        return None
    
    def _calculate_confidence(self, validated: Dict, ocr_data: Dict, llm_data: Dict) -> float:
        """
        Calculate confidence score (0-100)
        """
        score = 100.0
        
        # Check critical fields
        for field in self.CRITICAL_FIELDS:
            ocr_val = ocr_data.get(field)
            llm_val = llm_data.get(field)
            validated_val = validated.get(field)
            
            if validated_val is None:
                score -= 30  # Missing critical field
            elif ocr_val != llm_val:
                score -= 10  # Conflict in critical field
        
        # Check important fields
        for field in self.IMPORTANT_FIELDS:
            ocr_val = ocr_data.get(field)
            llm_val = llm_data.get(field)
            validated_val = validated.get(field)
            
            if validated_val is None:
                score -= 5  # Missing important field
            elif ocr_val != llm_val:
                score -= 3  # Conflict in important field
        
        # Bonus for complete data
        complete_fields = sum(1 for f in self.CRITICAL_FIELDS if validated.get(f) is not None)
        if complete_fields == len(self.CRITICAL_FIELDS):
            score += 10
        
        # Penalty for conflicts
        score -= len(self.conflicts) * 5
        
        return max(0.0, min(100.0, score))
    
    def get_validation_report(self) -> Dict:
        """
        Get detailed validation report
        """
        return {
            'total_conflicts': len(self.conflicts),
            'conflicts': self.conflicts,
            'resolutions': self.resolutions,
        }