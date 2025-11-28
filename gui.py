import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
import subprocess
import threading
import os
import sys

class TranslationApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Novel Translator")
        self.root.geometry("800x600")

        # --- Configuration Section ---
        config_frame = tk.LabelFrame(root, text="Configuration", padx=10, pady=10)
        config_frame.pack(fill="x", padx=10, pady=5)

        # Mode Selection
        self.mode_var = tk.StringVar(value="library")
        tk.Label(config_frame, text="Mode:").grid(row=0, column=0, sticky="w")
        tk.Radiobutton(config_frame, text="Library (Multiple Books)", variable=self.mode_var, value="library").grid(row=0, column=1, sticky="w")
        tk.Radiobutton(config_frame, text="Single Book", variable=self.mode_var, value="book").grid(row=0, column=2, sticky="w")

        # Directory Selection
        tk.Label(config_frame, text="Directory:").grid(row=1, column=0, sticky="w")
        self.dir_entry = tk.Entry(config_frame, width=50)
        self.dir_entry.grid(row=1, column=1, columnspan=2, padx=5, pady=5)
        tk.Button(config_frame, text="Browse...", command=self.browse_directory).grid(row=1, column=3, padx=5)

        # Options
        self.force_var = tk.BooleanVar()
        self.fix_only_var = tk.BooleanVar()
        self.limit_var = tk.StringVar()

        tk.Checkbutton(config_frame, text="Force Re-translation", variable=self.force_var).grid(row=2, column=0, columnspan=2, sticky="w")
        tk.Checkbutton(config_frame, text="Fix Broken Only", variable=self.fix_only_var).grid(row=2, column=2, sticky="w")
        
        tk.Label(config_frame, text="Chapter Limit (Optional):").grid(row=3, column=0, sticky="w")
        tk.Entry(config_frame, textvariable=self.limit_var, width=10).grid(row=3, column=1, sticky="w")

        # --- Actions Section ---
        action_frame = tk.Frame(root, padx=10, pady=10)
        action_frame.pack(fill="x", padx=10)

        self.start_btn = tk.Button(action_frame, text="Start Translation", command=self.start_translation, bg="#4CAF50", fg="white", font=("Arial", 12, "bold"))
        self.start_btn.pack(side="left", padx=5)

        self.open_site_btn = tk.Button(action_frame, text="Open Website", command=self.open_website, state="disabled")
        self.open_site_btn.pack(side="left", padx=5)

        # --- Output Section ---
        output_frame = tk.LabelFrame(root, text="Progress Log", padx=10, pady=10)
        output_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.log_area = scrolledtext.ScrolledText(output_frame, state='disabled', height=15)
        self.log_area.pack(fill="both", expand=True)

    def browse_directory(self):
        directory = filedialog.askdirectory()
        if directory:
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, directory)

    def log(self, message):
        self.log_area.config(state='normal')
        self.log_area.insert(tk.END, message)
        self.log_area.see(tk.END)
        self.log_area.config(state='disabled')

    def start_translation(self):
        directory = self.dir_entry.get()
        if not directory:
            messagebox.showerror("Error", "Please select a directory.")
            return

        self.start_btn.config(state="disabled")
        self.open_site_btn.config(state="disabled")
        self.log_area.config(state='normal')
        self.log_area.delete(1.0, tk.END)
        self.log_area.config(state='disabled')

        # Determine python executable
        python_exe = sys.executable
        
        # Check for local venv and prefer it
        venv_python = os.path.join(os.getcwd(), ".venv", "Scripts", "python.exe")
        if os.path.exists(venv_python):
            python_exe = venv_python
            
        # Build command
        cmd = [python_exe, "translate_epub.py"]
        
        if self.mode_var.get() == "library":
            cmd.extend(["--library_dir", directory])
        else:
            cmd.extend(["--book_dir", directory])

        if self.force_var.get():
            cmd.append("--force")
        
        if self.fix_only_var.get():
            cmd.append("--fix-only")

        if self.limit_var.get():
            try:
                int(self.limit_var.get())
                cmd.extend(["--limit", self.limit_var.get()])
            except ValueError:
                messagebox.showerror("Error", "Limit must be a number.")
                self.start_btn.config(state="normal")
                return

        # Run in thread
        thread = threading.Thread(target=self.run_process, args=(cmd,))
        thread.start()

    def run_process(self, cmd):
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            for line in process.stdout:
                self.root.after(0, self.log, line)

            process.wait()
            
            self.root.after(0, self.on_process_complete, process.returncode)

        except Exception as e:
            self.root.after(0, self.log, f"Error: {str(e)}\n")
            self.root.after(0, self.on_process_complete, -1)

    def on_process_complete(self, returncode):
        self.start_btn.config(state="normal")
        if returncode == 0:
            self.log("\nProcess completed successfully.\n")
            self.open_site_btn.config(state="normal")
            messagebox.showinfo("Success", "Translation completed!")
        else:
            self.log(f"\nProcess failed with exit code {returncode}.\n")
            messagebox.showerror("Error", "Translation failed. Check logs.")

    def open_website(self):
        directory = self.dir_entry.get()
        if self.mode_var.get() == "library":
            index_path = os.path.join(directory, "index.html")
        else:
            index_path = os.path.join(directory, "docs", "index.html")
        
        if os.path.exists(index_path):
            os.startfile(index_path)
        else:
            messagebox.showerror("Error", f"Index file not found at {index_path}")

if __name__ == "__main__":
    root = tk.Tk()
    app = TranslationApp(root)
    root.mainloop()
