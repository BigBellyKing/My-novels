import os
import json
import time
import argparse
import ctypes
import threading
import generate_site
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

# --- CONFIGURATION: SAMBANOVA ---
API_KEY = os.getenv("SAMBANOVA_API_KEY")
BASE_URL = "https://api.sambanova.ai/v1"

# Choice: DeepSeek-V3-0324 (Best Quality) or Meta-Llama-3.3-70B-Instruct (Fastest)
MODEL_NAME = "DeepSeek-V3-0324" 

# SambaNova Free Tier Limits
# They allow burst speed (RPM), but strict Daily Limits (RPD)
DAILY_REQUEST_LIMIT = 40
DAILY_TOKEN_LIMIT = 200000

DEFAULT_RAW_DIR = "raw_chapters"
DEFAULT_TRANSLATED_DIR = "translated_chapters"
DEFAULT_GLOSSARY_FILE = "glossary.json"

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL
)

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
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    if len(lines) < 10: return False
    for i in range(len(lines) - 5):
        chunk = lines[i:i+5]
        if i + 10 <= len(lines):
            next_chunk = lines[i+5:i+10]
            if chunk == next_chunk: return True
    return False

def check_refusal(text):
    refusal_keywords = ["I cannot translate", "I can't translate", "content policy", "safety guidelines"]
    lower_text = text.lower()
    return any(k in lower_text for k in refusal_keywords)

def validate_translation(filepath, source_text=None):
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
                if len(source_text) > 0:
                    ratio = len(content) / len(source_text)
                    if ratio < 0.6: 
                        print(f"❌ Text too short ({ratio:.2f}x). Likely summary.")
                        return False
                
                source_lines = len([x for x in source_text.split('\n') if x.strip()])
                trans_lines = len([x for x in content.split('\n') if x.strip()])
                
                # Strict check for SambaNova because it is fast and might skip
                if source_lines > 20 and trans_lines < (source_lines * 0.5):
                    print(f"❌ Paragraph mismatch (Source: {source_lines}, Trans: {trans_lines}). Content skipped.")
                    return False

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
    relevant = {}
    for term, translation in full_glossary.items():
        if term in text:
            relevant[term] = translation
    return relevant

def estimate_tokens(text):
    # CJK char approx 1.3 tokens on Llama/DeepSeek tokenizers
    return int(len(text) * 1.3)

# --- GLOBAL COUNTERS ---
# Since SambaNova has a hard daily limit, we track this session's usage
session_requests = 0
session_tokens = 0

def check_session_limits(estimated_tokens):
    global session_requests, session_tokens
    if session_requests >= DAILY_REQUEST_LIMIT:
        print(f"\n⚠️  DAILY LIMIT REACHED (Requests: {session_requests}). Stopping script.")
        return False
    if session_tokens + estimated_tokens >= DAILY_TOKEN_LIMIT:
        print(f"\n⚠️  DAILY TOKEN LIMIT REACHED (Tokens: {session_tokens}). Stopping script.")
        return False
    return True

# --- MAIN PROCESS ---

def process_chapter(chapter_filename, glossary, raw_dir, translated_dir, glossary_path):
    global session_requests, session_tokens
    
    raw_path = os.path.join(raw_dir, chapter_filename)
    translated_path = os.path.join(translated_dir, chapter_filename)
    
    with open(raw_path, "r", encoding="utf-8") as f:
        text = f.read()

    current_glossary = filter_glossary(text, glossary)
    
    # Approx tokens
    total_est = estimate_tokens(text) + 2000 
    
    # Check limits before we start
    if not check_session_limits(total_est):
        return "STOP"
    
    system_prompt = f"""
    You are a professional novel translator (Chinese to English).
    
    CRITICAL INSTRUCTIONS:
    1. **NO SUMMARIZATION:** Translate every single sentence. Do not skip scenes.
    2. **FORMAT:** Output strict Markdown. Use double newlines for paragraphs.
    3. **GLOSSARY:** Strictly follow these terms:
    {json.dumps(current_glossary, ensure_ascii=False)}
    
    4. **OUTPUT FORMAT:** Return ONLY a valid JSON object.
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
            print(f"[{chapter_filename}] Sending to SambaNova ({MODEL_NAME})...")
            
            response = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                model=MODEL_NAME,
                response_format={"type": "json_object"}, 
                temperature=0.7
            )
            
            # Increment Counters
            session_requests += 1
            # SambaNova doesn't always return usage in the same format, so we estimate or read from response
            if response.usage:
                session_tokens += response.usage.total_tokens
            else:
                session_tokens += total_est

            response_content = response.choices[0].message.content
            result = json.loads(response_content)
            
            final_text = result.get("translated_text", "")
            new_terms_list = result.get("new_terms", [])
            
            with open(translated_path, "w", encoding="utf-8") as f:
                f.write(final_text)
            
            if validate_translation(translated_path, source_text=text):
                if new_terms_list:
                    with glossary_lock:
                        unique_terms = {t['original_term']: t['english_translation'] for t in new_terms_list}
                        if unique_terms:
                            print(f"[{chapter_filename}] Found {len(unique_terms)} new terms.")
                            for k, v in unique_terms.items():
                                glossary[k] = v
                            save_glossary(glossary, glossary_path)
                print(f"[{chapter_filename}] ✅ DONE. (Session Req: {session_requests}/{DAILY_REQUEST_LIMIT})")
                return "SUCCESS"
            else:
                print(f"[{chapter_filename}] Validation failed (Attempt {attempt+1}).")
                time.sleep(2)
                
        except Exception as e:
            print(f"[{chapter_filename}] Error: {e}")
            if "429" in str(e):
                print("🚨 429 Rate Limit Hit. This likely means your Daily Quota is fully used.")
                return "STOP"
            time.sleep(5)

    print(f"[{chapter_filename}] ❌ FAILED after retries.")
    return "FAILED"

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

    for chapter_file in chapters:
        try:
            chapter_num = int(chapter_file.split("_")[1].split(".")[0])
        except: continue
        
        if args.chapters and chapter_num not in args.chapters: continue

        translated_path = os.path.join(translated_dir, chapter_file)
        
        if os.path.exists(translated_path):
            if not args.force:
                if args.audit or args.fix_only:
                    try:
                        with open(os.path.join(raw_dir, chapter_file), "r", encoding="utf-8") as f:
                            src = f.read()
                        if validate_translation(translated_path, source_text=src):
                            continue
                    except: pass 
                else:
                    continue

        chapters_to_translate.append(chapter_file)

    if args.limit and not args.chapters:
        chapters_to_translate = chapters_to_translate[:args.limit]

    print(f"Queue size: {len(chapters_to_translate)}")
    if args.audit: return

    for chapter_file in chapters_to_translate:
        status = process_chapter(chapter_file, glossary, raw_dir, translated_dir, glossary_path)
        if status == "STOP":
            print("🛑 Stopping translation session due to limits.")
            break

    try:
        generate_site.generate_site(source_dir=translated_dir, output_dir=os.path.join(book_dir, "docs"))
    except Exception: pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--chapters", type=int, nargs="+")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--fix-only", action="store_true")
    parser.add_argument("--book_dir", type=str)
    parser.add_argument("--library_dir", type=str)
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