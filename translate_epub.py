import os
import json
import google.generativeai as genai
from dotenv import load_dotenv
import time
import typing_extensions as typing
import argparse
import ctypes
import threading
import generate_site

# Load environment variables
load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    print("WARNING: GEMINI_API_KEY not found in .env file. Ensure it is set in environment.")

try:
    genai.configure(api_key=API_KEY)
except Exception as e:
    print(f"Error configuring API: {e}")

# Configuration
MODEL_NAME = "gemini-2.5-flash" 
# Configuration
MODEL_NAME = "gemini-2.5-flash" 
DEFAULT_RAW_DIR = "raw_chapters"
DEFAULT_TRANSLATED_DIR = "translated_chapters"
DEFAULT_GLOSSARY_FILE = "glossary.json"

# Thread synchronization (still good practice even if sequential, for future proofing)
glossary_lock = threading.Lock()

# Windows Sleep Prevention Constants
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

def prevent_sleep():
    """Prevents the system from entering sleep mode."""
    try:
        print("Preventing system sleep...")
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except AttributeError:
        pass

def allow_sleep():
    """Allows the system to sleep again."""
    try:
        print("Allowing system sleep...")
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except AttributeError:
        pass

# Define the output schema
class TermEntry(typing.TypedDict):
    original_term: str
    english_translation: str

class TranslationOutput(typing.TypedDict):
    translated_text: str
    new_terms: list[TermEntry]
    thought: str  # Added for Chain of Thought

class RateLimiter:
    """
    Simple Rate Limiter for RPM and TPM.
    """
    def __init__(self, rpm_limit=10, tpm_limit=100000):
        self.rpm_limit = rpm_limit
        self.tpm_limit = tpm_limit
        self.request_timestamps = []
        self.token_timestamps = [] # List of (timestamp, token_count)
        self.lock = threading.Lock()

    def _cleanup(self):
        now = time.time()
        # Remove timestamps older than 60 seconds
        self.request_timestamps = [t for t in self.request_timestamps if now - t < 60]
        self.token_timestamps = [(t, c) for t, c in self.token_timestamps if now - t < 60]

    def wait_if_needed(self, estimated_tokens=0):
        with self.lock:
            while True:
                self._cleanup()
                
                # Check RPM
                current_rpm = len(self.request_timestamps)
                
                # Check TPM
                current_tpm = sum(c for t, c in self.token_timestamps)
                
                if current_rpm < self.rpm_limit and (current_tpm + estimated_tokens) <= self.tpm_limit:
                    break
                
                # Wait a bit
                time.sleep(1)
            
            # Record this request
            now = time.time()
            self.request_timestamps.append(now)
            if estimated_tokens > 0:
                self.token_timestamps.append((now, estimated_tokens))

# Global Rate Limiter
rate_limiter = RateLimiter(rpm_limit=10, tpm_limit=100000)

def check_hallucination(text):
    """
    Simple check for repetitive loops (hallucinations).
    Returns True if hallucination detected.
    """
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    if len(lines) < 10:
        return False
        
    # Check for immediate repetition of the same line multiple times
    for i in range(len(lines) - 5):
        chunk = lines[i:i+5]
        # If the next 5 lines are exactly the same as this chunk
        if i + 10 <= len(lines):
            next_chunk = lines[i+5:i+10]
            if chunk == next_chunk and chunk[0] == chunk[1]: # Strong repetition
                 return True
                 
    # Check for single line repeating many times
    from collections import Counter
    counts = Counter(lines)
    most_common = counts.most_common(1)
    if most_common:
        line, count = most_common[0]
        if count > 10 and len(line) > 5: # Arbitrary threshold
            return True
            
    return False

def check_refusal(text):
    """
    Checks if the text looks like an AI refusal.
    """
    refusal_keywords = [
        "I cannot translate", "I can't translate", "I am unable to translate",
        "As an AI language model",
        "violate my safety guidelines", "against my content policy"
    ]
    lower_text = text.lower()
    for keyword in refusal_keywords:
        if keyword.lower() in lower_text:
            return True
    return False

def load_glossary(glossary_path):
    if os.path.exists(glossary_path):
        with open(glossary_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_glossary(glossary, glossary_path):
    with open(glossary_path, "w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, indent=4)

def validate_translation(filepath, source_text=None):
    """
    Checks if the translation file seems complete and valid.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            
            if not content.strip():
                print(f"Validation Failed: Empty content in {filepath}")
                return False
            
            # 1. Refusal Check
            if check_refusal(content):
                print(f"Validation Failed: AI Refusal detected in {filepath}")
                return False

            # 2. Hallucination Check
            if check_hallucination(content):
                print(f"Validation Failed: Hallucination detected in {filepath}")
                return False

            # 3. Length Ratio Check (if source provided)
            if source_text:
                len_source = len(source_text)
                len_trans = len(content)
                if len_source > 0:
                    ratio = len_trans / len_source
                    if ratio < 0.2: # Too short
                        print(f"Validation Failed: Translation too short ({ratio:.2f}) in {filepath}")
                        return False
                    if ratio > 5.0: # Too long (suspicious)
                        print(f"Validation Failed: Translation too long ({ratio:.2f}) in {filepath}")
                        return False

            # 4. End Marker Check
            # Primary check: <<END_OF_CHAPTER>>
            if "<<END_OF_CHAPTER>>" in content:
                return True
                
            # Secondary checks
            content_lower = content.lower()
            if "(end of chapter)" in content_lower or "(end of this chapter)" in content_lower:
                return True
            if "(本章完)" in content:
                return True
            
            print(f"Validation Failed: Missing End Marker in {filepath}")
            return False
    except Exception as e:
        print(f"Error validating {filepath}: {e}")
        return False

def process_chapter(chapter_filename, glossary, raw_dir, translated_dir, glossary_path):
    """
    Handles the full process for a single chapter: Translate Full Text -> Save -> Update Glossary.
    """
    raw_path = os.path.join(raw_dir, chapter_filename)
    translated_path = os.path.join(translated_dir, chapter_filename)
    
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
    - Translate "(本章完)" as "(End of Chapter)".
    - At the very end of translated_text, append the literal string <<END_OF_CHAPTER>> on its own line.
    
    You MUST strictly follow this glossary of names/terms:
    {json.dumps(glossary, ensure_ascii=False)}
    
    If you encounter NEW proper nouns (names, places, specific terminology) that are NOT in the glossary:
    1. Translate them consistently within this chapter.
    2. Add them to the 'new_terms' list in your output.
    
    Return the result in JSON format with three fields:
    - "thought": A brief reasoning about the translation approach for this chapter.
    - "translated_text": The full English translation in Markdown format.
    - "new_terms": A list of objects, each with "original_term" and "english_translation".
    
    Original Text:
    {text}
    """

    max_retries = 2
    base_delay = 10
    
    # Estimate tokens (rough char count / 4)
    estimated_tokens = len(prompt) // 4

    for attempt in range(max_retries):
        try:
            # Rate Limiting
            rate_limiter.wait_if_needed(estimated_tokens)

            print(f"[{chapter_filename}] Translating (Attempt {attempt + 1})...")
            response = model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=TranslationOutput
                )
            )
            
            result = json.loads(response.text)
            
            final_text = result["translated_text"]
            new_terms_list = result.get("new_terms", [])
            
            # Temporary save for validation
            with open(translated_path, "w", encoding="utf-8") as f:
                f.write(final_text)
                
            # Validate
            if validate_translation(translated_path, source_text=text):
                # Success!
                
                # Update Glossary (Thread-Safe)
                if new_terms_list:
                    with glossary_lock:
                        # Deduplicate terms
                        unique_terms = {t['original_term']: t['english_translation'] for t in new_terms_list}
                        
                        print(f"[{chapter_filename}] Found {len(unique_terms)} new terms. Updating glossary...")
                        for original, english in unique_terms.items():
                            glossary[original] = english
                        save_glossary(glossary, glossary_path)
                
                print(f"[{chapter_filename}] DONE and Saved.")
                return # Exit function on success
            else:
                print(f"[{chapter_filename}] Validation failed. Retrying...")
                # If it was a validation failure, we might want to wait a bit or adjust prompt (not implemented here)
                time.sleep(5)
            
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "Resource has been exhausted" in error_str:
                wait_time = base_delay * (2 ** attempt)
                print(f"[{chapter_filename}] Rate limit hit. Waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"[{chapter_filename}] Error: {e}")
                # Don't return immediately, try to retry if it's a transient error
                time.sleep(5)

    print(f"[{chapter_filename}] Failed after {max_retries} retries.")

def process_book(book_dir, args):
    """
    Processes a single book directory.
    """
    print(f"\n--- Processing Book: {book_dir} ---")
    
    raw_dir = os.path.join(book_dir, DEFAULT_RAW_DIR)
    translated_dir = os.path.join(book_dir, DEFAULT_TRANSLATED_DIR)
    glossary_path = os.path.join(book_dir, DEFAULT_GLOSSARY_FILE)
    
    # Fallback to global glossary if book-specific one doesn't exist
    if not os.path.exists(glossary_path) and os.path.exists(DEFAULT_GLOSSARY_FILE):
         # We might want to copy the global one or just use it read-only? 
         # For now, let's just use the book specific path, creating it if needed.
         pass

    os.makedirs(translated_dir, exist_ok=True)
    glossary = load_glossary(glossary_path)

    # Get list of chapters and sort them
    if not os.path.exists(raw_dir):
        print(f"Directory {raw_dir} not found. Skipping.")
        return

    chapters = sorted([f for f in os.listdir(raw_dir) if f.endswith(".txt")])

    # Filter chapters
    chapters_to_translate = []
    for chapter_file in chapters:
        # Extract number from filename "chapter_001.txt"
        try:
            chapter_num = int(chapter_file.split("_")[1].split(".")[0])
        except:
            continue

        translated_path = os.path.join(translated_dir, chapter_file)

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
        print("No chapters to translate for this book.")
        # Even if no chapters translated, we might want to regen site if files exist
    else:
        print(f"Starting translation for {book_dir}.")
        print(f"Chapters to process: {len(chapters_to_translate)}")

        try:
            # SEQUENTIAL PROCESSING
            for i, chapter_file in enumerate(chapters_to_translate):
                print(f"\nProcessing {chapter_file} ({i+1}/{len(chapters_to_translate)})...")
                process_chapter(chapter_file, glossary, raw_dir, translated_dir, glossary_path)
        except Exception as e:
            print(f"Error processing book {book_dir}: {e}")

    # Automatically generate site for this book
    print(f"\nTriggering site regeneration for {book_dir}...")
    try:
        output_dir = os.path.join(book_dir, "docs")
        generate_site.generate_site(source_dir=translated_dir, output_dir=output_dir)
    except Exception as e:
        print(f"Error generating site: {e}")


def main():
    parser = argparse.ArgumentParser(description="Translate novel chapters.")
    parser.add_argument("--limit", type=int, help="Limit the number of chapters to translate")
    parser.add_argument("--chapters", type=int, nargs="+", help="Specific chapter numbers to translate (e.g. 1 5 10)")
    parser.add_argument("--force", action="store_true", help="Force re-translation even if file exists")
    parser.add_argument("--fix-only", action="store_true", help="Only re-translate broken chapters, do not translate new ones")
    
    # New arguments for multiple books
    parser.add_argument("--book_dir", type=str, help="Path to a specific book directory")
    parser.add_argument("--library_dir", type=str, help="Path to a directory containing multiple books")
    
    args = parser.parse_args()

    prevent_sleep()
    
    try:
        if args.library_dir:
            if not os.path.exists(args.library_dir):
                print(f"Library directory {args.library_dir} not found.")
                return
            
            subdirs = [os.path.join(args.library_dir, d) for d in os.listdir(args.library_dir) if os.path.isdir(os.path.join(args.library_dir, d))]
            print(f"Found {len(subdirs)} books in library.")
            
            for book_dir in subdirs:
                process_book(book_dir, args)
                
        elif args.book_dir:
            if not os.path.exists(args.book_dir):
                print(f"Book directory {args.book_dir} not found.")
                return
            process_book(args.book_dir, args)
            
        else:
            # Default behavior: current directory
            process_book(".", args)
            
    finally:
        allow_sleep()
        print("Translation session ended.")

if __name__ == "__main__":
    main()
