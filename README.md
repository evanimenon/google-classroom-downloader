# Google Classroom Auto File Downloader

Automatically download all attachments (assignments, materials, links, PDFs, Docs, Slides, etc.) from your Google Classroom courses using the official Google Classroom + Drive APIs.

---

## Features

- Downloads **all Drive attachments** from every Classroom course and organizes downloads into folders per course  
- Converts Google Docs → PDF, Slides → PPTX, Sheets → XLSX  
- Skips previously downloaded files automatically  
- Filters by course name  
- Fully secure OAuth authentication and 100% compliant with Google API policies  

---

## Project Structure

```

classroom-downloader/
│
├── classroom_downloader.py     # Main script
├── requirements.txt            # Dependencies
├── .gitignore        
└── README.md

````

---

## !!! Security Warning !!!

This script uses OAuth tokens.  
**NEVER upload these files to GitHub:**

- `credentials.json`  
- `token.json`  
- `download_index.json`  
- Everything inside `downloads/`  

The provided `.gitignore` already protects you.

!!! If you accidentally commit these files, revoke the OAuth credential immediately and re-generate it. !!!

---

## Installation

### 1. Clone this repository

```bash
git clone https://github.com/evanimenon/google-classroom-downloader.git
cd google-classroom-downloader
````

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

(Optional virtual environment)

```bash
python3 -m venv venv
source venv/bin/activate   # macOS / Linux
# OR
venv\Scripts\activate      # Windows
```

---

## Google OAuth Setup (One-Time)

Follow these steps carefully.

### 1. Open Google Cloud Console
[https://console.cloud.google.com/](https://console.cloud.google.com/)

<img width="1446" height="893" alt="Screenshot 2025-11-30 at 01 57 31" src="https://github.com/user-attachments/assets/d695f9a7-6bac-422e-872e-54da2210b76d" />


### 2. Create a New Project
**IAM & Admin → Create Project**
Project name: `Classroom Downloader`
<img width="1446" height="891" alt="Screenshot 2025-11-30 at 02 01 39" src="https://github.com/user-attachments/assets/7997fa7a-d279-4ec6-bfad-b1ea076c255a" />


### 3. Enable APIs

Go to: **APIs & Services → Enable APIs & Services**

Enable:

* **Google Classroom API**
* **Google Drive API**
<img width="1444" height="891" alt="Screenshot 2025-11-30 at 02 03 16" src="https://github.com/user-attachments/assets/2028fdd2-322d-44a7-b31f-287ab0a91eeb" />

### 4. Configure OAuth Consent Screen

Left sidebar → **OAuth consent screen**

* User Type: **External**
* App Name: `Classroom Downloader`
* Add your email under **Test Users**
* Save

### 5. Create OAuth Desktop Credentials

Left sidebar → **APIs and Services → OAuth Consent Screen → Clients → Create Client**

* Application type: **Desktop App**
* Name: `Classroom Desktop`
* Click **Download JSON**
<img width="1447" height="892" alt="Screenshot 2025-11-30 at 02 03 54" src="https://github.com/user-attachments/assets/bea31c22-dfca-4484-b020-95c4e51e2267" />

Rename the downloaded file to:

```
credentials.json
```

Place it in the project root:

```
classroom-downloader/credentials.json
```

**!!! Do NOT commit this file !!!**

---

## Running the Downloader

Run:

```bash
python classroom_downloader.py
```

On first run:

* A browser window appears
* Log in with your Google account
* Approve the requested permissions
* `token.json` is generated automatically

Downloads will appear under:

```
downloads/
    <Course Name>/
        file1.pdf
        file2.docx
```

---

## Advanced Usage

### Download only one course

```bash
python classroom_downloader.py --course-name-contains "CSE201"
```

### Run without downloading (simulation)

```bash
python classroom_downloader.py --dry-run
```

### Change download directory

```bash
python classroom_downloader.py --base-dir "/Users/me/ClassroomFiles"
```

---

Contributions welcome!
(Planning on adding GUI launcher, auto-run cron scheduler, custom export formats, sync logs, etc.)

