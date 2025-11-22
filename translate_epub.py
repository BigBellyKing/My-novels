import os
import json
import google.generativeai as genai
from dotenv import load_dotenv
import time
import typing_extensions as typing

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

def translate_chapter(chapter_filename, glossary):
    raw_path = os.path.join(RAW_DIR, chapter_filename)
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

    try:
        print(f"Translating {chapter_filename}...")
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
        print(f"Error translating {chapter_filename}: {e}")
        return None

import argparse

def main():
    parser = argparse.ArgumentParser(description="Translate novel chapters.")
    parser.add_argument("--limit", type=int, help="Limit the number of chapters to translate")
    args = parser.parse_args()

    os.makedirs(TRANSLATED_DIR, exist_ok=True)
    glossary = load_glossary()
    
    # Get list of chapters and sort them
    chapters = sorted([f for f in os.listdir(RAW_DIR) if f.endswith(".txt")])
    
    count = 0
    for chapter_file in chapters:
        if args.limit and count >= args.limit:
            print(f"Reached limit of {args.limit} chapters.")
            break

        translated_path = os.path.join(TRANSLATED_DIR, chapter_file)
        
        # Skip if already translated
        if os.path.exists(translated_path):
            print(f"Skipping {chapter_file} (already translated)")
            continue
            
        result = translate_chapter(chapter_file, glossary)
        
        if result:
            # Save translated text
            with open(translated_path, "w", encoding="utf-8") as f:
                f.write(result["translated_text"])
            
            # Update glossary with new terms
            new_terms_list = result.get("new_terms", [])
            if new_terms_list:
                print(f"Found {len(new_terms_list)} new terms. Updating glossary...")
                for term in new_terms_list:
                    glossary[term["original_term"]] = term["english_translation"]
                save_glossary(glossary)
            
            print(f"Saved {chapter_file}")
            count += 1
            
            # Rate limit protection
            time.sleep(2)

if __name__ == "__main__":
    main()
