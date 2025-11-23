import os

TRANSLATED_DIR = "translated_chapters"

def check_translations():
    if not os.path.exists(TRANSLATED_DIR):
        print(f"Directory '{TRANSLATED_DIR}' not found.")
        return

    files = sorted([f for f in os.listdir(TRANSLATED_DIR) if f.endswith(".txt")])
    incomplete_chapters = []
    total_files = len(files)

    print(f"Scanning {total_files} files in '{TRANSLATED_DIR}'...\n")

    for filename in files:
        filepath = os.path.join(TRANSLATED_DIR, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                
                if not content.strip():
                    print(f"[EMPTY] {filename}")
                    incomplete_chapters.append(filename)
                    continue
                
                # Check for markers (case-insensitive)
                content_lower = content.lower()
                if "(end of chapter)" not in content_lower and "(end of this chapter)" not in content_lower and "(本章完)" not in content:
                    print(f"[INCOMPLETE] {filename}")
                    incomplete_chapters.append(filename)

        except Exception as e:
            print(f"[ERROR] Could not read {filename}: {e}")

    print("-" * 30)
    print(f"Scan Complete.")
    print(f"Total Files: {total_files}")
    print(f"Incomplete:  {len(incomplete_chapters)}")
    
    if incomplete_chapters:
        print("\nIncomplete Chapters:")
        for ch in incomplete_chapters:
            print(ch)
            
        # Extract numbers for easy copy-paste
        try:
            nums = [str(int(f.split('_')[1].split('.')[0])) for f in incomplete_chapters]
            print("\nCommand to re-translate these specific chapters:")
            print(f"python translate_epub.py --force --chapters {' '.join(nums)}")
        except:
            pass
    else:
        print("\nAll translated chapters look complete!")

if __name__ == "__main__":
    check_translations()
