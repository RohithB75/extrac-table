import os
import sys
import requests
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Attempt to retrieve the API key from the environment
API_KEY = os.getenv("API_KEY")
if not API_KEY:
    print("Error: API_KEY not found in environment variables.  Please create a .env file with API_KEY=YOUR_API_KEY")
    sys.exit(1)

BASE_URL = "https://api.pdf.co/v1"

def upload_file(path):
    """Uploads a file to the PDF.co server.

    Args:
        path (str): The path to the file to upload.

    Returns:
        str: The URL of the uploaded file on the server.

    Raises:
        RuntimeError: If the upload fails.
    """
    url = f"{BASE_URL}/file/upload"
    try:
        with open(path, "rb") as f:
            resp = requests.post(url,
                             headers={"x-api-key": API_KEY},
                             files={"file": f})
        resp.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        j = resp.json()
        if j.get("error"):
            raise RuntimeError(f"Upload failed: {j.get('message', '')}")
        return j["url"]
    except (requests.exceptions.RequestException, OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Error during upload: {e}")

def make_searchable(url, name):
    """Makes a PDF file searchable using OCR.

    Args:
        url (str): The URL of the PDF file to make searchable.
        name (str): The desired name for the searchable PDF.

    Returns:
        str: The URL of the searchable PDF.

    Raises:
        RuntimeError: If the operation fails.
    """
    ep = f"{BASE_URL}/pdf/makesearchable"
    payload = {"url": url, "name": name}
    try:
        resp = requests.post(ep,
                             headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
                             json=payload)
        resp.raise_for_status()
        j = resp.json()
        if j.get("error"):
            raise RuntimeError(f"MakeSearchable: {j.get('message', '')}")
        return j["url"]
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        raise RuntimeError(f"Error making PDF searchable: {e}")


def download_file(url, dest):
    """Downloads a file from a given URL to a destination path.

    Args:
        url (str): The URL of the file to download.
        dest (str): The destination path to save the downloaded file.

    Raises:
        RuntimeError: If the download fails.
    """
    try:
        resp = requests.get(url)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)
    except (requests.exceptions.RequestException, OSError) as e:
        raise RuntimeError(f"Error downloading file: {e}")


def extract_to_json2(url):
    """Extracts data from a PDF file to JSON2 format.

    Args:
        url (str): The URL of the PDF file.

    Returns:
        dict: The extracted data in JSON2 format.

    Raises:
        RuntimeError: If the extraction fails.
    """
    ep = f"{BASE_URL}/pdf/convert/to/json2"
    payload = {"url": url, "inline": True}
    try:
        resp = requests.post(ep,
                             headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
                             json=payload)
        resp.raise_for_status()
        j = resp.json()
        if j.get("error"):
            raise RuntimeError(f"Convert-to-JSON2: {j.get('message', '')}")
        return j["body"]  # this has .document.page
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        raise RuntimeError(f"Error extracting data to JSON: {e}")



def parse_json2_tables(body):
    """Parses tables from JSON2 data.

    Args:
        body (dict): The JSON2 data containing the table information.
                      Expected structure (simplified):
                      {
                        "document": {
                          "page": [
                            {
                              "index": int,
                              "row": [
                                {
                                  "column": [
                                    {
                                      "text": str or {"text": str}
                                    },
                                    ...
                                  ]
                                },
                                ...
                              ]
                            },
                            ...
                          ]
                        }
                      }

    Returns:
        list: A list of dictionaries, where each dictionary represents a table
              found in the PDF.  Each table is represented as:
              {
                "page": int,
                "headers": list of str,
                "rows": list of list of str
              }
              Returns an empty list if no tables are found.
    """
    docs = body.get("document", {})
    pages = docs.get("page", [])  # Changed to .get() to handle missing "page"
    if not isinstance(pages, list): #checks if pages is a list
        if isinstance(pages, dict):
            pages = [pages] #if pages is a dict, convert it to a list
        else:
            return [] #if pages is neither a list nor a dict, return empty list
    results = []
    for pg in pages:
        if not isinstance(pg, dict):  # Ensure pg is a dictionary
            continue
        idx = int(pg.get("index", 0)) + 1  # 1-based page number
        rows = pg.get("row", [])
        if not isinstance(rows, list): # Check if rows is a list.
            continue
        if not rows:
            continue
        # Build matrix of text
        mat = []
        for r in rows:
            if not isinstance(r, dict): # Check if r is a dictionary
                continue
            cols = []
            for cell in r.get("column", []):
                if not isinstance(cell, dict):  # Ensure cell is a dictionary
                    continue
                # cell["text"] may be string or object
                txt = cell.get("text", "")
                if isinstance(txt, dict):
                    txt = txt.get("text", "")
                cols.append(str(txt).strip())  # Ensure txt is a string
            mat.append(cols)
        if not mat:
            continue
        # First row = headers
        headers = mat[0]
        data = mat[1:]
        results.append({
            "page": idx,
            "headers": headers,
            "rows": data
        })
    return results



def main():
    """Main function to orchestrate the PDF processing."""
    if len(sys.argv) != 2:
        print("Usage: python convert_and_parse.py scanned.pdf")
        sys.exit(1)

    inp = sys.argv[1]
    if not os.path.exists(inp):
        print("Not found:", inp)
        sys.exit(1)

    try:
        print("1️⃣ Uploading…")
        up_url = upload_file(inp)

        base, name = os.path.splitext(os.path.basename(inp))
        searchable_name = f"{base}_searchable.pdf"

        print("2️⃣ OCR→ searchable PDF…")
        searchable_url = make_searchable(up_url, searchable_name)

        print("3️⃣ Downloading searchable PDF…")
        download_file(searchable_url, searchable_name)
        print("  Saved:", searchable_name)

        print("4️⃣ Extracting tables via JSON2…")
        body = extract_to_json2(searchable_url)

        print("5️⃣ Parsing tables…")
        tables = parse_json2_tables(body)

        out = "parsed_tables.json"
        with open(out, "w") as f:
            json.dump(tables, f, indent=4)
        print("✅ Parsed tables written to", out)

    except RuntimeError as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
