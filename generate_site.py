import os
import markdown
import shutil

TRANSLATED_DIR = "translated_chapters"
OUTPUT_DIR = "docs"
TEMPLATE_DIR = "templates"

# Simple CSS for mobile-friendly reading with Dark Mode support
CSS = """
:root {
    --bg-color: #f4f4f4;
    --container-bg: #fff;
    --text-color: #333;
    --heading-color: #2c3e50;
    --link-color: #3498db;
    --nav-border: #eee;
    --chapter-link-bg: #f9f9f9;
    --chapter-link-hover: #e9e9e9;
}

[data-theme="dark"] {
    --bg-color: #1a1a1a;
    --container-bg: #2d2d2d;
    --text-color: #e0e0e0;
    --heading-color: #ecf0f1;
    --link-color: #5dade2;
    --nav-border: #444;
    --chapter-link-bg: #3d3d3d;
    --chapter-link-hover: #4d4d4d;
}

body {
    font-family: 'Merriweather', Georgia, serif;
    line-height: 1.8;
    max-width: 800px;
    margin: 0 auto;
    padding: 20px;
    background-color: var(--bg-color);
    color: var(--text-color);
    transition: background-color 0.3s, color 0.3s;
}
.container {
    background-color: var(--container-bg);
    padding: 30px;
    border-radius: 8px;
    box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    transition: background-color 0.3s;
}
h1 {
    text-align: center;
    color: var(--heading-color);
    margin-bottom: 30px;
}
p {
    margin-bottom: 1.5em;
}
.nav {
    display: flex;
    justify-content: space-between;
    margin-top: 40px;
    padding-top: 20px;
    border-top: 1px solid var(--nav-border);
}
.nav a {
    text-decoration: none;
    color: var(--link-color);
    font-weight: bold;
}
.nav a:hover {
    text-decoration: underline;
}
.chapter-list {
    list-style: none;
    padding: 0;
}
.chapter-list li {
    margin-bottom: 10px;
}
.chapter-list a {
    text-decoration: none;
    color: var(--text-color);
    display: block;
    padding: 10px;
    background: var(--chapter-link-bg);
    border-radius: 4px;
    transition: background 0.2s;
}
.chapter-list a:hover {
    background: var(--chapter-link-hover);
}
.theme-toggle {
    position: fixed;
    top: 20px;
    right: 20px;
    padding: 10px;
    border-radius: 50%;
    background: var(--container-bg);
    border: 1px solid var(--nav-border);
    cursor: pointer;
    box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    font-size: 1.2em;
    z-index: 100;
}
"""

JS_SCRIPT = """
<script>
    const toggleButton = document.getElementById('theme-toggle');
    const currentTheme = localStorage.getItem('theme');

    if (currentTheme) {
        document.documentElement.setAttribute('data-theme', currentTheme);
        toggleButton.textContent = currentTheme === 'dark' ? '☀️' : '🌙';
    }

    toggleButton.addEventListener('click', () => {
        let theme = document.documentElement.getAttribute('data-theme');
        if (theme === 'dark') {
            document.documentElement.setAttribute('data-theme', 'light');
            localStorage.setItem('theme', 'light');
            toggleButton.textContent = '🌙';
        } else {
            document.documentElement.setAttribute('data-theme', 'dark');
            localStorage.setItem('theme', 'dark');
            toggleButton.textContent = '☀️';
        }
    });
</script>
"""

def generate_site():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Write CSS
    with open(os.path.join(OUTPUT_DIR, "style.css"), "w", encoding="utf-8") as f:
        f.write(CSS)

    chapters = sorted([f for f in os.listdir(TRANSLATED_DIR) if f.endswith(".txt")])
    
    # Generate Index
    index_html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Novel Translations</title>
        <link rel="stylesheet" href="style.css">
    </head>
    <body>
        <button id="theme-toggle" class="theme-toggle">🌙</button>
        <div class="container">
            <h1>Translated Chapters</h1>
            <ul class="chapter-list">
    """
    
    for i, chapter_file in enumerate(chapters):
        chapter_num = i + 1
        link = f"chapter_{chapter_num:03}.html"
        index_html += f'<li><a href="{link}">Chapter {chapter_num}</a></li>\n'
        
    index_html += f"""
            </ul>
        </div>
        {JS_SCRIPT}
    </body>
    </html>
    """
    
    with open(os.path.join(OUTPUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)
        
    # Generate Chapter Pages
    for i, chapter_file in enumerate(chapters):
        chapter_num = i + 1
        prev_link = f"chapter_{chapter_num-1:03}.html" if i > 0 else "#"
        next_link = f"chapter_{chapter_num+1:03}.html" if i < len(chapters) - 1 else "#"
        
        with open(os.path.join(TRANSLATED_DIR, chapter_file), "r", encoding="utf-8") as f:
            md_content = f.read()
            
        html_content = markdown.markdown(md_content)
        
        page_html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Chapter {chapter_num}</title>
            <link rel="stylesheet" href="style.css">
            <link href="https://fonts.googleapis.com/css2?family=Merriweather:wght@300;400;700&display=swap" rel="stylesheet">
        </head>
        <body>
            <button id="theme-toggle" class="theme-toggle">🌙</button>
            <div class="container">
                <div class="nav">
                    <a href="index.html">Home</a>
                </div>
                {html_content}
                <div class="nav">
                    <a href="{prev_link}" style="visibility: {'visible' if i > 0 else 'hidden'}">← Previous</a>
                    <a href="{next_link}" style="visibility: {'visible' if i < len(chapters) - 1 else 'hidden'}">Next →</a>
                </div>
            </div>
            {JS_SCRIPT}
        </body>
        </html>
        """
        
        output_filename = f"chapter_{chapter_num:03}.html"
        with open(os.path.join(OUTPUT_DIR, output_filename), "w", encoding="utf-8") as f:
            f.write(page_html)
            
    print(f"Site generated in '{OUTPUT_DIR}' folder.")

if __name__ == "__main__":
    generate_site()
