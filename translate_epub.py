import os
import json
import google.generativeai as genai
from dotenv import load_dotenv
import time
import typing_extensions as typing
import argparse
import concurrent.futures
import ctypes
import threading

# Load environment variables
load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("GEMINI_API_KEY not found in .env file")

genai.configure(api_key=API_KEY)

# Configuration
MODEL_NAME = "gemini-2.5-flash" 
RAW_DIR = "raw_chapters"
TRANSLATED_DIR = "translated_chapters"
GLOSSARY_FILE = "glossary.json"

# Parallel Execution Config
MAX_WORKERS = 3           # Max simultaneous translations
CHUNK_SIZE = 4000         # Characters per chunk (approx 2-3k tokens)

# Thread synchronization
glossary_lock = threading.Lock()

# Windows Sleep Prevention Constants
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

def prevent_sleep():
    """Prevents the system from entering sleep mode."""
    print("Preventing system sleep...")
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)

def allow_sleep():
    """Allows the system to sleep again."""
    print("Allowing system sleep...")
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

# Define the output schema
class TermEntry(typing.TypedDict):
    original_term: str
    english_translation: str

class TranslationOutput(typing.TypedDict):
    translated_text: str
    new_terms: list[TermEntry]

def load_glossary():
    if os.path.exists(GLOSSARY_FILE):
        with open(GLOSSARY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_glossary(glossary):
    with open(GLOSSARY_FILE, "w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, indent=4)

def split_text(text, max_chunk_size):
    """
    Splits text into chunks, respecting paragraph boundaries (double newlines).
    """
    paragraphs = text.split('\n\n')
    chunks = []
    current_chunk = []
    current_length = 0
    
    for para in paragraphs:
        # +2 for the newlines we'll add back
        para_len = len(para) + 2
        
        if current_length + para_len > max_chunk_size and current_chunk:
            # Chunk is full, save it
            chunks.append('\n\n'.join(current_chunk))
            current_chunk = []
            current_length = 0
            
        current_chunk.append(para)
        current_length += para_len
        
    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))
        
    return chunks

def validate_translation(filepath):
    """
    Checks if the translation file seems complete by looking for the end marker.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            
            if not content.strip():
                return False
            
            # Check for the specific marker the user mentioned (case-insensitive)
            content_lower = content.lower()
            if "(end of chapter)" in content_lower or "(end of this chapter)" in content_lower:
                return True
            # Also check for the original marker in case it wasn't translated
            if "(本章完)" in content:
                return True
                
            return False
    except Exception as e:
        print(f"Error validating {filepath}: {e}")
        return False

def translate_chunk(text_chunk, glossary, chapter_filename, chunk_index, total_chunks):
    """
    Translates a single chunk of text.
    """
    model = genai.GenerativeModel(MODEL_NAME)

    prompt = f"""
    Translate the following novel chapter segment into English. 
    Maintain the nuance, tone, and style of the original.
    
    IMPORTANT: The output "translated_text" MUST be in Markdown format.
    - Use double newlines (\\n\\n) to separate paragraphs.
    - Do NOT collapse the text into a single block.
    - Preserve the dialogue structure.
    - Translate "(本章完)" as "(End of Chapter)".
    
    You MUST strictly follow this glossary of names/terms:
    {json.dumps(glossary, ensure_ascii=False)}
    
    If you encounter NEW proper nouns (names, places, specific terminology) that are NOT in the glossary:
    1. Translate them consistently within this chapter.
    2. Add them to the 'new_terms' list in your output.
    
    Return the result in JSON format with two fields:
    - "translated_text": The full English translation in Markdown format.
    - "new_terms": A list of objects, each with "original_term" and "english_translation".
    
    Original Text Segment:
    {text_chunk}
    """

    max_retries = 5
    base_delay = 10

    for attempt in range(max_retries):
        try:
            print(f"[{chapter_filename}] Translating Part {chunk_index+1}/{total_chunks} (Attempt {attempt + 1})...")
            response = model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=TranslationOutput
                )
            )
            
            result = json.loads(response.text)
            return result
            
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "Resource has been exhausted" in error_str:
                wait_time = base_delay * (2 ** attempt)
                print(f"[{chapter_filename}] Rate limit hit. Waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"[{chapter_filename}] Error in chunk {chunk_index+1}: {e}")
                return None
    
    return None

def process_chapter(chapter_filename, glossary):
    """
    Handles the full process for a single chapter: Split -> Translate Chunks -> Combine -> Save.
    """
    raw_path = os.path.join(RAW_DIR, chapter_filename)
    translated_path = os.path.join(TRANSLATED_DIR, chapter_filename)
    
    with open(raw_path, "r", encoding="utf-8") as f:
        text = f.read()

    # Split text into chunks
    chunks = split_text(text, CHUNK_SIZE)
    total_chunks = len(chunks)
    
    full_translated_text = []
    all_new_terms = []
    
    success = True
    
    for i, chunk in enumerate(chunks):
        result = translate_chunk(chunk, glossary, chapter_filename, i, total_chunks)
        
        if result:
            full_translated_text.append(result["translated_text"])
            if "new_terms" in result:
                all_new_terms.extend(result["new_terms"])
        else:
            print(f"[{chapter_filename}] Failed to translate chunk {i+1}. Aborting chapter.")
            success = False
            break
            
    if success:
        # Combine all parts
        final_text = "\n\n".join(full_translated_text)
        
        # Validate Content Before Saving
        is_valid = False
        content_lower = final_text.lower()
        if "(end of chapter)" in content_lower or "(end of this chapter)" in content_lower or "(本章完)" in final_text:
            is_valid = True
        else:
            print(f"[{chapter_filename}] WARNING: Missing end marker after translation.")
            
            # Attempt to retry the LAST chunk only
            if chunks:
                print(f"[{chapter_filename}] Retrying last chunk to fix missing marker...")
                last_chunk_idx = len(chunks) - 1
                retry_result = translate_chunk(chunks[last_chunk_idx], glossary, chapter_filename, last_chunk_idx, total_chunks)
                
                if retry_result:
                    # Check if the new last chunk fixes it
                    new_last_text = retry_result["translated_text"]
                    if "(end of chapter)" in new_last_text.lower() or "(end of this chapter)" in new_last_text.lower() or "(本章完)" in new_last_text:
                        full_translated_text[last_chunk_idx] = new_last_text
                        final_text = "\n\n".join(full_translated_text)
                        is_valid = True
                        print(f"[{chapter_filename}] Retry successful! Marker found.")
                    else:
                         print(f"[{chapter_filename}] Retry failed. Still missing marker.")
        
        if not is_valid:
            print(f"[{chapter_filename}] CRITICAL: Validation failed. Saving anyway (as requested) to preserve partial translation.")
            # We do NOT return here anymore. We save what we have.

        # 1. Save Translated Text
        try:
            with open(translated_path, "w", encoding="utf-8") as f:
                f.write(final_text)
        except Exception as e:
            print(f"[{chapter_filename}] Error saving file: {e}")
            return

        # 2. Update Glossary (Thread-Safe)
        if all_new_terms:
            with glossary_lock:
                # Deduplicate terms
                unique_terms = {t['original_term']: t['english_translation'] for t in all_new_terms}
                
                print(f"[{chapter_filename}] Found {len(unique_terms)} new terms. Updating glossary...")
                for original, english in unique_terms.items():
                    glossary[original] = english
                save_glossary(glossary)
        
        print(f"[{chapter_filename}] DONE and Saved ({total_chunks} parts).")

def main():
    parser = argparse.ArgumentParser(description="Translate novel chapters.")
    parser.add_argument("--limit", type=int, help="Limit the number of chapters to translate")
    parser.add_argument("--chapters", type=int, nargs="+", help="Specific chapter numbers to translate (e.g. 1 5 10)")
    parser.add_argument("--force", action="store_true", help="Force re-translation even if file exists")
    parser.add_argument("--fix-only", action="store_true", help="Only re-translate broken chapters, do not translate new ones")
    args = parser.parse_args()

    os.makedirs(TRANSLATED_DIR, exist_ok=True)
    glossary = load_glossary()
    
    # Get list of chapters and sort them
    chapters = sorted([f for f in os.listdir(RAW_DIR) if f.endswith(".txt")])
    
    # Filter chapters
    chapters_to_translate = []
    for chapter_file in chapters:
        # Extract number from filename "chapter_001.txt"
        try:
            chapter_num = int(chapter_file.split("_")[1].split(".")[0])
        except:
            continue

        translated_path = os.path.join(TRANSLATED_DIR, chapter_file)
        
        # Check if this chapter is selected
        if args.chapters and chapter_num not in args.chapters:
            continue
            
        # Check if it already exists
        if os.path.exists(translated_path):
            if not args.force:
                # Check if the existing translation is complete
                if validate_translation(translated_path):
                    if args.chapters and chapter_num in args.chapters:
                         print(f"Skipping {chapter_file} (already translated and valid). Use --force to overwrite.")
                    continue
                else:
                    print(f"Retranslating {chapter_file} (found incomplete translation).")
        else:
            # File does not exist
            if args.fix_only:
                continue
            
        chapters_to_translate.append(chapter_file)

    if args.limit and not args.chapters:
        chapters_to_translate = chapters_to_translate[:args.limit]

    if not chapters_to_translate:
        print("No chapters to translate.")
        return

    print(f"Starting translation.")
    print(f"Chapters to process: {[f for f in chapters_to_translate]}")
    print(f"Max Concurrent: {MAX_WORKERS}")
    print(f"Chunk Size:     {CHUNK_SIZE} chars")
    
    prevent_sleep()
    
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = []
            for i, chapter_file in enumerate(chapters_to_translate):
                print(f"\nScheduling {chapter_file} ({i+1}/{len(chapters_to_translate)})...")
                
                # Submit task
                future = executor.submit(process_chapter, chapter_file, glossary)
                futures.append(future)
            
            print("\nAll chapters scheduled. Waiting for completion...")
            concurrent.futures.wait(futures)
            
    finally:
        allow_sleep()
        print("Translation session ended.")

import generate_site

if __name__ == "__main__":
    main()
    
    # Automatically generate site
    print("\nTriggering site regeneration...")
    try:
        generate_site.generate_site()
    except Exception as e:
        print(f"Error generating site: {e}")
