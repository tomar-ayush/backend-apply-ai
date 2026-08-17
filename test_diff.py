import re

def extract_bullets(latex: str):
    # Match \resumeItem{...} or \item ...
    # Wait, \resumeItem{...} might span multiple lines
    # It's better to just split by \resumeItem or \item and see
    pass

