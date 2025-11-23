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
STAGGER_DELAY = 30        # Wait 30s before starting the next chapter

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
    # We load the glossary once at the start. 
    # In a threaded environment, we rely on the in-memory dict updated under lock.
    if os.path.exists(GLOSSARY_FILE):
        with open(GLOSSARY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_glossary(glossary):
    with open(GLOSSARY_FILE, "w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, indent=4)

def process_chapter(chapter_filename, glossary):
    """
    Handles the full process for a single chapter: Translate -> Save -> Update Glossary.
    This runs inside the worker thread.
    """
    raw_path = os.path.join(RAW_DIR, chapter_filename)
    translated_path = os.path.join(TRANSLATED_DIR, chapter_filename)
    
    with open(raw_path, "r", encoding="utf-8") as f:
        text = f.read()

    model = genai.GenerativeModel(MODEL_NAME)

    prompt = f"""
    Translate the following novel chapter into English. 
    Maintain the nuance, tone, and style of the original.
    
    IMPORTANT: The output "translated_text" MUST be in Markdown format.
    - Use double newlines (\\n\\n) to separate paragraphs.
    - Do NOT collapse the text into a single block.
    - Preserve the dialogue structure.
    
    You MUST strictly follow this glossary of names/terms:
    {json.dumps(glossary, ensure_ascii=False)}
    
    If you encounter NEW proper nouns (names, places, specific terminology) that are NOT in the glossary:
    1. Translate them consistently within this chapter.
    2. Add them to the 'new_terms' list in your output.
    
    Return the result in JSON format with two fields:
    - "translated_text": The full English translation in Markdown format.
    - "new_terms": A list of objects, each with "original_term" and "english_translation".
    
    Original Text:
    {text}
    """

    max_retries = 5
    base_delay = 10

    result = None
    for attempt in range(max_retries):
        try:
            print(f"[{chapter_filename}] Translating (Attempt {attempt + 1})...")
            response = model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=TranslationOutput
                )
            )
            
            result = json.loads(response.text)
            break # Success
            
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "Resource has been exhausted" in error_str:
                wait_time = base_delay * (2 ** attempt)
                print(f"[{chapter_filename}] Rate limit hit. Waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"[{chapter_filename}] Error: {e}")
                return # Fatal error for this chapter

    if result:
        # 1. Save Translated Text
        try:
            with open(translated_path, "w", encoding="utf-8") as f:
                f.write(result["translated_text"])
        except Exception as e:
            print(f"[{chapter_filename}] Error saving file: {e}")
            return

        # 2. Update Glossary (Thread-Safe)
        new_terms_list = result.get("new_terms", [])
        if new_terms_list:
            with glossary_lock:
                print(f"[{chapter_filename}] Found {len(new_terms_list)} new terms. Updating glossary...")
                for term in new_terms_list:
                    glossary[term["original_term"]] = term["english_translation"]
                save_glossary(glossary)
        
        print(f"[{chapter_filename}] DONE and Saved.")
    else:
        print(f"[{chapter_filename}] Failed after retries.")

def main():
    parser = argparse.ArgumentParser(description="Translate novel chapters.")
    parser.add_argument("--limit", type=int, help="Limit the number of chapters to translate")
    parser.add_argument("--chapters", type=int, nargs="+", help="Specific chapter numbers to translate (e.g. 1 5 10)")
    parser.add_argument("--force", action="store_true", help="Force re-translation even if file exists")
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
        if os.path.exists(translated_path) and not args.force:
            # If user specifically asked for this chapter but didn't use force, warn them
            if args.chapters and chapter_num in args.chapters:
                print(f"Skipping {chapter_file} (already translated). Use --force to overwrite.")
            continue
            
        chapters_to_translate.append(chapter_file)

    if args.limit and not args.chapters:
        chapters_to_translate = chapters_to_translate[:args.limit]

    if not chapters_to_translate:
        print("No chapters to translate.")
        return

    print(f"Starting staggered translation.")
    print(f"Chapters to process: {[f for f in chapters_to_translate]}")
    print(f"Max Concurrent: {MAX_WORKERS}")
    print(f"Stagger Delay:  {STAGGER_DELAY}s")
    
    prevent_sleep()
    
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = []
            for i, chapter_file in enumerate(chapters_to_translate):
                print(f"\nScheduling {chapter_file} ({i+1}/{len(chapters_to_translate)})...")
                
                # Submit task
                future = executor.submit(process_chapter, chapter_file, glossary)
                futures.append(future)
                
                # Wait before scheduling the next one (Staggered Start)
                # We don't wait after the very last one
                if i < len(chapters_to_translate) - 1:
                    time.sleep(STAGGER_DELAY)
            
            print("\nAll chapters scheduled. Waiting for completion...")
            # Wait for all tasks to finish
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
