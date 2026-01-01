import os
import json
import time
import typing_extensions as typing
import argparse
import ctypes
import threading
import generate_site
from dotenv import load_dotenv

# --- NEW: Cerebras Import ---
try:
    from cerebras.cloud.sdk import Cerebras
except ImportError:
    print("CRITICAL: Cerebras SDK not found. Please run 'pip install cerebras_cloud_sdk'")
    exit(1)

# Load environment variables
load_dotenv()

API_KEY = os.getenv("CEREBRAS_API_KEY")
if not API_KEY:
    print("WARNING: CEREBRAS_API_KEY not found in .env file.")

# --- NEW: Cerebras Client Setup ---
client = Cerebras(
    api_key=API_KEY,
)

# Configuration
# User specified model
MODEL_NAME = "qwen-3-235b-a22b-instruct-2507" 
DEFAULT_RAW_DIR = "raw_chapters"
DEFAULT_TRANSLATED_DIR = "translated_chapters"
DEFAULT_GLOSSARY_FILE = "glossary.json"

# Thread synchronization
glossary_lock = threading.Lock()

# Windows Sleep Prevention
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

def prevent_sleep():
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except AttributeError:
        pass

def allow_sleep():
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except AttributeError:
        pass

# --- VALIDATION LOGIC ---

def check_hallucination(text):
    """Checks for repetitive loops."""
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    if len(lines) < 10: return False
    
    # Check for immediate repetition
    for i in range(len(lines) - 5):
        chunk = lines[i:i+5]
        if i + 10 <= len(lines):
            next_chunk = lines[i+5:i+10]
            if chunk == next_chunk: return True
    return False

def check_refusal(text):
    refusal_keywords = [
        "I cannot translate", "I can't translate", "unable to translate",
        "AI language model", "content policy", "safety guidelines"
    ]
    lower_text = text.lower()
    return any(k in lower_text for k in refusal_keywords)

def validate_translation(filepath, source_text=None):
    """
    Robustly checks if translation is valid and complete.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            
            if not content.strip():
                print(f"❌ Empty content: {filepath}")
                return False
            
            if check_refusal(content):
                print(f"❌ Refusal detected: {filepath}")
                return False

            if check_hallucination(content):
                print(f"❌ Hallucination detected: {filepath}")
                return False

            if source_text:
                # 1. Length Ratio Check
                # English is usually longer than CJK. < 0.6 is suspicious.
                if len(source_text) > 0:
                    ratio = len(content) / len(source_text)
                    if ratio < 0.6: 
                        print(f"❌ Text too short ({ratio:.2f}x). Likely summary.")
                        return False
                
                # 2. Line Count Check (The "Middle Skip" Detector)
                source_lines = len([x for x in source_text.split('\n') if x.strip()])
                trans_lines = len([x for x in content.split('\n') if x.strip()])
                
                # Allow some consolidation, but < 50% usually means skipped content
                if source_lines > 20 and trans_lines < (source_lines * 0.5):
                    print(f"❌ Paragraph mismatch (Source: {source_lines}, Trans: {trans_lines}). Content skipped.")
                    return False

            # 3. End Marker Check
            if "<<END_OF_CHAPTER>>" in content:
                return True
            
            print(f"❌ Missing <<END_OF_CHAPTER>> marker: {filepath}")
            return False

    except Exception as e:
        print(f"Error validating {filepath}: {e}")
        return False

# --- UTILS ---

def load_glossary(glossary_path):
    if os.path.exists(glossary_path):
        with open(glossary_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_glossary(glossary, glossary_path):
    with open(glossary_path, "w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False, indent=4)

def filter_glossary(text, full_glossary):
    """Returns only glossary terms that appear in the text to save tokens."""
    relevant = {}
    for term, translation in full_glossary.items():
        if term in text:
            relevant[term] = translation
    return relevant

def estimate_tokens(text):
    """
    Heuristic for token counting (approximate).
    CJK char ~= 1.5 tokens (safe upper bound for Qwen)
    English word ~= 1.3 tokens
    """
    return int(len(text) * 1.5)

# --- RATE LIMITER ---

class RateLimiter:
    """
    Strict Rate Limiter for Cerebras.
    Limits: RPM 30, TPM 64,000
    """
    def __init__(self, rpm_limit=30, tpm_limit=64000):
        self.rpm_limit = rpm_limit
        self.tpm_limit = tpm_limit
        self.request_timestamps = []
        self.token_timestamps = [] 
        self.lock = threading.Lock()

    def _cleanup(self):
        now = time.time()
        # Keep data only for the last 60 seconds
        self.request_timestamps = [t for t in self.request_timestamps if now - t < 60]
        self.token_timestamps = [(t, c) for t, c in self.token_timestamps if now - t < 60]

    def wait_if_needed(self, estimated_tokens=0):
        with self.lock:
            while True:
                self._cleanup()
                current_rpm = len(self.request_timestamps)
                current_tpm = sum(c for t, c in self.token_timestamps)
                
                # Check limits
                if current_rpm < self.rpm_limit and (current_tpm + estimated_tokens) <= self.tpm_limit:
                    break
                
                # Debug print if waiting
                if current_rpm >= self.rpm_limit:
                    print(f"   [Limit] RPM Hit ({current_rpm}/{self.rpm_limit}). Waiting...")
                if (current_tpm + estimated_tokens) > self.tpm_limit:
                    print(f"   [Limit] TPM Hit ({current_tpm + estimated_tokens}/{self.tpm_limit}). Waiting...")
                
                time.sleep(2) # Wait 2 seconds before checking again
            
            # Record usage
            now = time.time()
            self.request_timestamps.append(now)
            self.token_timestamps.append((now, estimated_tokens))

# Initialize with User Constraints
rate_limiter = RateLimiter(rpm_limit=30, tpm_limit=64000)

# --- MAIN PROCESS ---

def process_chapter(chapter_filename, glossary, raw_dir, translated_dir, glossary_path):
    raw_path = os.path.join(raw_dir, chapter_filename)
    translated_path = os.path.join(translated_dir, chapter_filename)
    
    with open(raw_path, "r", encoding="utf-8") as f:
        text = f.read()

    # Optimization: Filter glossary to save tokens
    current_glossary = filter_glossary(text, glossary)

    # 1. Estimate Tokens (Input + Expected Output)
    input_est = estimate_tokens(text) + estimate_tokens(json.dumps(current_glossary)) + 500 # System prompt buffer
    output_est = int(estimate_tokens(text) * 1.5) # Output usually larger than input
    total_est = input_est + output_est
    
    # Check if a single chapter exceeds the TPM limit alone
    if total_est > 64000:
        print(f"⚠️  WARNING: Chapter {chapter_filename} estimated {total_est} tokens. This exceeds the 1-minute global limit.")
        # We proceed, but the rate limiter will block subsequent requests for > 60s
    
    system_prompt = f"""
    You are a professional novel translator.
    
    CRITICAL INSTRUCTIONS:
    1. **NO SUMMARIZATION:** Translate every single sentence. Do not skip scenes.
    2. **FORMAT:** Output strict Markdown. Use double newlines for paragraphs.
    3. **GLOSSARY:** Strictly follow these terms:
    {json.dumps(current_glossary, ensure_ascii=False)}
    
    4. **OUTPUT FORMAT:** Return ONLY a valid JSON object. Do not wrap in markdown code blocks like ```json.
    Structure:
    {{
        "translated_text": "The full markdown translation... <<END_OF_CHAPTER>>",
        "new_terms": [{{"original_term": "Name", "english_translation": "Name"}}]
    }}
    
    Append <<END_OF_CHAPTER>> at the very end of the translated text string.
    """

    user_prompt = f"Translate:\n\n{text}"

    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            rate_limiter.wait_if_needed(total_est)
            
            print(f"[{chapter_filename}] Sending to Cerebras (Est. {total_est} tokens)...")
            
            # Cerebras Chat Completion Call
            response = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                model=MODEL_NAME,
                response_format={"type": "json_object"}, # Enforce JSON
                temperature=0.7,
                top_p=0.9
            )
            
            # Parse Response
            response_content = response.choices[0].message.content
            result = json.loads(response_content)
            
            final_text = result.get("translated_text", "")
            new_terms_list = result.get("new_terms", [])
            
            # Save
            with open(translated_path, "w", encoding="utf-8") as f:
                f.write(final_text)
            
            # Validate
            if validate_translation(translated_path, source_text=text):
                # Update Glossary
                if new_terms_list:
                    with glossary_lock:
                        unique_terms = {t['original_term']: t['english_translation'] for t in new_terms_list}
                        if unique_terms:
                            print(f"[{chapter_filename}] Found {len(unique_terms)} new terms.")
                            for k, v in unique_terms.items():
                                glossary[k] = v
                            save_glossary(glossary, glossary_path)
                
                print(f"[{chapter_filename}] ✅ DONE.")
                return 
            else:
                print(f"[{chapter_filename}] Validation failed (Attempt {attempt+1}).")
                time.sleep(2)
                
        except Exception as e:
            print(f"[{chapter_filename}] Error: {e}")
            if "429" in str(e):
                print("Rate limit hit hard. Sleeping 30s...")
                time.sleep(30)
            else:
                time.sleep(5)

    print(f"[{chapter_filename}] ❌ FAILED after retries.")

def process_book(book_dir, args):
    print(f"\n--- Processing Book: {book_dir} ---")
    raw_dir = os.path.join(book_dir, DEFAULT_RAW_DIR)
    translated_dir = os.path.join(book_dir, DEFAULT_TRANSLATED_DIR)
    glossary_path = os.path.join(book_dir, DEFAULT_GLOSSARY_FILE)
    
    os.makedirs(translated_dir, exist_ok=True)
    glossary = load_glossary(glossary_path)

    if not os.path.exists(raw_dir): return

    chapters = sorted([f for f in os.listdir(raw_dir) if f.endswith(".txt")])
    chapters_to_translate = []
    
    # Validation stats
    passed = 0
    failed = 0

    print("Checking existing files...")

    for chapter_file in chapters:
        try:
            chapter_num = int(chapter_file.split("_")[1].split(".")[0])
        except: continue

        if args.chapters and chapter_num not in args.chapters: continue

        raw_path = os.path.join(raw_dir, chapter_file)
        translated_path = os.path.join(translated_dir, chapter_file)
        
        # We MUST load source text to perform strict validation (ratio/line count)
        try:
            with open(raw_path, "r", encoding="utf-8") as f:
                source_text = f.read()
        except Exception:
            print(f"Skipping {chapter_file} (Cannot read raw).")
            continue

        if os.path.exists(translated_path):
            if not args.force:
                # DEEP VALIDATION: Pass source_text to check ratios/line counts
                if validate_translation(translated_path, source_text=source_text):
                    passed += 1
                    # It's good! Skip it.
                    continue 
                else:
                    print(f"⚠️  Existing translation for {chapter_file} is invalid/incomplete.")
                    failed += 1
                    # It failed. If audit mode, just log it. If not, add to queue.
                    if args.audit:
                        continue
        else:
            # File doesn't exist
            if args.fix_only or args.audit:
                continue
        
        # If we are here, it needs translation
        chapters_to_translate.append(chapter_file)

    if args.audit:
        print(f"\n[AUDIT REPORT] Passed: {passed} | Failed/Missing: {failed}")
        print("Run without --audit to fix the failed chapters.")
        return

    if args.limit and not args.chapters:
        chapters_to_translate = chapters_to_translate[:args.limit]

    print(f"\nQueue size: {len(chapters_to_translate)} chapters to translate.")

    for i, chapter_file in enumerate(chapters_to_translate):
        process_chapter(chapter_file, glossary, raw_dir, translated_dir, glossary_path)

    # Site Generation
    try:
        generate_site.generate_site(source_dir=translated_dir, output_dir=os.path.join(book_dir, "docs"))
    except Exception: pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--chapters", type=int, nargs="+")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--book_dir", type=str)
    parser.add_argument("--library_dir", type=str)
    parser.add_argument("--fix-only", action="store_true", help="Only fix broken chapters, do not translate new ones.")
    parser.add_argument("--audit", action="store_true", help="Run validation only and report failures. Does not translate.")
    args = parser.parse_args()

    prevent_sleep()
    try:
        if args.library_dir and os.path.exists(args.library_dir):
            subdirs = [os.path.join(args.library_dir, d) for d in os.listdir(args.library_dir) if os.path.isdir(os.path.join(args.library_dir, d))]
            for book in subdirs: process_book(book, args)
            generate_site.generate_library_index(args.library_dir, subdirs)
        elif args.book_dir and os.path.exists(args.book_dir):
            process_book(args.book_dir, args)
        else:
            process_book(".", args)
    finally:
        allow_sleep()
        print("Session ended.")

if __name__ == "__main__":
    main()