"""
CRS Comment Cleaning - Test Script
Demonstrates the improvements in comment extraction and cleaning
"""

from apps.crs_documents.helpers.comment_cleaner import get_comment_cleaner

def test_comment_cleaning():
    """Test the enhanced comment cleaning functionality"""
    
    # Initialize cleaner
    cleaner = get_comment_cleaner()
    
    # Test cases from expert feedback
    test_cases = [
        # Test Case 1: Text Box Removal
        {
            "input": "text box Update the design specifications",
            "expected": "Update the design specifications",
            "description": "Remove 'text box' from beginning"
        },
        {
            "input": "Text Box Check valve sizing",
            "expected": "Check valve sizing",
            "description": "Remove 'Text Box' with capital letters"
        },
        
        # Test Case 2: Callout Removal
        {
            "input": "Callout: Verify pressure ratings",
            "expected": "Verify pressure ratings",
            "description": "Remove 'Callout:' from beginning"
        },
        {
            "input": "callout Update P&ID drawing",
            "expected": "Update P&ID drawing",
            "description": "Remove lowercase 'callout'"
        },
        
        # Test Case 3: Free Text Removal
        {
            "input": "Free Text Review calculations",
            "expected": "Review calculations",
            "description": "Remove 'Free Text' from beginning"
        },
        {
            "input": "free text: Check specifications",
            "expected": "Check specifications",
            "description": "Remove 'free text:' with colon"
        },
        
        # Test Case 4: Note/Sticky Note Removal
        {
            "input": "Note Verify the design",
            "expected": "Verify the design",
            "description": "Remove 'Note' from beginning"
        },
        {
            "input": "Sticky Note: Update equipment list",
            "expected": "Update equipment list",
            "description": "Remove 'Sticky Note:'"
        },
        
        # Test Case 5: Name Removal from Beginning
        {
            "input": "Sreejith Rajeev - Update design",
            "expected": "Update design",
            "description": "Remove name from beginning with hyphen"
        },
        {
            "input": "John Smith: Check specifications",
            "expected": "Check specifications",
            "description": "Remove Western name with colon"
        },
        {
            "input": "Ahmed Ali text box Verify ratings",
            "expected": "Verify ratings",
            "description": "Remove name and annotation label"
        },
        
        # Test Case 6: Name Preservation in Middle/End
        {
            "input": "Contact Ahmed for approval",
            "expected": "Contact Ahmed for approval",
            "description": "Keep name when in middle of sentence"
        },
        {
            "input": "Review with John before finalizing",
            "expected": "Review with John before finalizing",
            "description": "Keep name when part of actual comment"
        },
        
        # Test Case 7: Combined Patterns
        {
            "input": "John Smith Callout: text box Update the P&ID",
            "expected": "Update the P&ID",
            "description": "Remove name, callout, and text box"
        },
        {
            "input": "Sreejith Rajeev Free Text: Review calculations with Ahmed",
            "expected": "Review calculations with Ahmed",
            "description": "Remove name and free text, keep name in content"
        },
        
        # Test Case 8: Skip Cases (Should be marked as skip)
        {
            "input": "text box",
            "should_skip": True,
            "description": "Skip standalone 'text box'"
        },
        {
            "input": "Callout",
            "should_skip": True,
            "description": "Skip standalone 'Callout'"
        },
        {
            "input": "Typewriter 166",
            "should_skip": True,
            "description": "Skip technical element"
        },
    ]
    
    print("=" * 80)
    print("CRS COMMENT CLEANING TEST RESULTS")
    print("=" * 80)
    print()
    
    passed = 0
    failed = 0
    
    for i, test in enumerate(test_cases, 1):
        print(f"Test {i}: {test['description']}")
        print(f"  Input:    '{test['input']}'")
        
        result = cleaner.clean_comment(test['input'])
        
        if 'should_skip' in test and test['should_skip']:
            if result.should_skip:
                print(f"  Result:   ✅ SKIP (Reason: {result.skip_reason})")
                print(f"  Status:   ✅ PASSED")
                passed += 1
            else:
                print(f"  Result:   ❌ '{result.cleaned_text}'")
                print(f"  Expected: SKIP")
                print(f"  Status:   ❌ FAILED")
                failed += 1
        else:
            expected = test['expected']
            actual = result.cleaned_text
            
            if actual == expected:
                print(f"  Result:   ✅ '{actual}'")
                print(f"  Method:   {result.cleaning_method}")
                print(f"  Status:   ✅ PASSED")
                passed += 1
            else:
                print(f"  Result:   ❌ '{actual}'")
                print(f"  Expected: '{expected}'")
                print(f"  Status:   ❌ FAILED")
                failed += 1
        
        print()
    
    print("=" * 80)
    print(f"SUMMARY: {passed}/{passed + failed} tests passed")
    print("=" * 80)
    
    return passed, failed


if __name__ == "__main__":
    try:
        passed, failed = test_comment_cleaning()
        
        if failed == 0:
            print("\n🎉 All tests passed! CRS comment cleaning is working perfectly.")
            exit(0)
        else:
            print(f"\n⚠️ {failed} test(s) failed. Please review the results above.")
            exit(1)
    except Exception as e:
        print(f"\n❌ Error running tests: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
