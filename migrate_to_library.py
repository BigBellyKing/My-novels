import os
import shutil
import sys

def migrate():
    # Define paths
    current_dir = os.getcwd()
    library_dir = os.path.join(current_dir, "docs")
    book_dir = os.path.join(library_dir, "Book1")
    
    # Folders to move
    folders_to_move = ["raw_chapters", "translated_chapters", "docs"]
    files_to_move = ["glossary.json"]
    
    # Create destination directories
    if not os.path.exists(library_dir):
        print(f"Creating Library directory: {library_dir}")
        os.makedirs(library_dir)
        
    if not os.path.exists(book_dir):
        print(f"Creating Book directory: {book_dir}")
        os.makedirs(book_dir)
        
    # Move folders
    for folder in folders_to_move:
        src = os.path.join(current_dir, folder)
        dst = os.path.join(book_dir, folder)
        
        if os.path.exists(src):
            print(f"Moving {folder} to {dst}...")
            if os.path.exists(dst):
                print(f"  Destination {dst} already exists. Merging/Overwriting...")
                # shutil.move fails if dst exists and is a dir, so we need to handle it
                for item in os.listdir(src):
                    s = os.path.join(src, item)
                    d = os.path.join(dst, item)
                    if os.path.exists(d):
                        if os.path.isdir(s):
                            shutil.rmtree(d)
                        else:
                            os.remove(d)
                    shutil.move(s, d)
                os.rmdir(src) # Remove empty source dir
            else:
                shutil.move(src, dst)
        else:
            print(f"  Source {folder} not found. Skipping.")
            
    # Move files
    for filename in files_to_move:
        src = os.path.join(current_dir, filename)
        dst = os.path.join(book_dir, filename)
        
        if os.path.exists(src):
            print(f"Moving {filename} to {dst}...")
            if os.path.exists(dst):
                print(f"  Destination {dst} already exists. Overwriting...")
                os.remove(dst)
            shutil.move(src, dst)
        else:
            print(f"  Source {filename} not found. Skipping.")

    print("\nMigration completed successfully!")
    print(f"Your files are now in: {book_dir}")
    print("You can rename 'Book1' to your novel's title.")

if __name__ == "__main__":
    migrate()
