import os
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import re

EPUB_PATH = "raw_chapters/Novel.epub"
OUTPUT_DIR = "raw_chapters"

def process_epub():
    """
    Extracts chapters from the EPUB file and saves them as text files.
    """
    if not os.path.exists(EPUB_PATH):
        print(f"Error: EPUB file not found at {EPUB_PATH}")
        return

    print(f"Processing {EPUB_PATH}...")
    try:
        book = epub.read_epub(EPUB_PATH)
    except Exception as e:
        print(f"Error reading EPUB: {e}")
        return
    
    chapter_count = 0
    
    # Iterate through items in the book
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            # Get HTML content
            content = item.get_content()
            soup = BeautifulSoup(content, 'html.parser')
            
            # Extract text
            text = soup.get_text(separator='\n\n', strip=True)
            
            # Basic filter: skip very short sections (likely TOC, cover, or empty)
            if len(text) < 100:
                continue
                
            chapter_count += 1
            filename = f"chapter_{chapter_count:03}.txt"
            filepath = os.path.join(OUTPUT_DIR, filename)
            
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(text)
            
            print(f"Saved: {filepath}")

    print(f"\nDone! Extracted {chapter_count} chapters to {OUTPUT_DIR}")

if __name__ == "__main__":
    process_epub()
