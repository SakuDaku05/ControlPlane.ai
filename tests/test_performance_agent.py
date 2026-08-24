import pytest
import re

def test_think_block_regex():
    text = "<think>\nThis is a thought process\n</think>\nThis is the actual answer."
    
    clean_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    assert clean_text == "This is the actual answer."

def test_think_block_incomplete_regex():
    text = "<think>\nThis is a thought process..."
    
    # First regex removes completed blocks
    clean_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    # Second regex removes incomplete blocks
    clean_text = re.sub(r'<think>.*', '', clean_text, flags=re.DOTALL).strip()
    
    assert clean_text == ""
