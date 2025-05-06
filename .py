import os
import sys
import requests
import json

API_KEY  = ""      # ← replace with your PDF.co key
BASE_URL = "https://api.pdf.co/v1"

def upload_file(path):
    url = f"{BASE_URL}/file/upload"
    resp = requests.post(url,
        headers={"x-api-key": API_KEY},
        files={"file": open(path,"rb")}
    )
    resp.raise_for_status()
    j = resp.json()
    if j.get("error"):
        raise RuntimeError("Upload failed: " + j.get("message",""))
    return j["url"]

def make_searchable(url, name):
    ep = f"{BASE_URL}/pdf/makesearchable"
    payload = {"url": url, "name": name}
    resp = requests.post(ep,
        headers={"x-api-key": API_KEY, "Content-Type":"application/json"},
        json=payload
    )
    resp.raise_for_status()
    j = resp.json()
    if j.get("error"):
        raise RuntimeError("MakeSearchable: " + j.get("message",""))
    return j["url"]

def download_file(url, dest):
    resp = requests.get(url)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        f.write(resp.content)

def extract_to_json2(url):
    ep = f"{BASE_URL}/pdf/convert/to/json2"
    payload = {"url": url, "inline": True}
    resp = requests.post(ep,
        headers={"x-api-key": API_KEY, "Content-Type":"application/json"},
        json=payload
    )
    resp.raise_for_status()
    j = resp.json()
    if j.get("error"):
        raise RuntimeError("Convert-to-JSON2: " + j.get("message",""))
    return j["body"]  # this has .document.page

def parse_json2_tables(body):
    """
    body = {
      "document": {
        "pageCount": "...",
        "pageCountWithOCRPerformed": "...",
        "page": [ { index, row: [ { column: [ {text or text{text} } ... ] } ... ] }, ... ]
      }
    }
    """
    docs = body.get("document", {})
    pages = docs.get("page")
    if isinstance(pages, dict):
        pages = [pages]
    results = []
    for pg in pages:
        idx = int(pg.get("index", 0)) + 1  # 1-based page number
        rows = pg.get("row", [])
        if not rows:
            continue
        # Build matrix of text
        mat = []
        for r in rows:
            cols = []
            for cell in r.get("column", []):
                # cell["text"] may be string or object
                txt = cell.get("text", "")
                if isinstance(txt, dict):
                    txt = txt.get("text","")
                cols.append(txt.strip())
            mat.append(cols)
        # First row = headers
        headers = mat[0]
        data    = mat[1:]
        results.append({
            "page":    idx,
            "headers": headers,
            "rows":    data
        })
    return results

def main():
    if len(sys.argv)!=2:
        print("Usage: python convert_and_parse.py scanned.pdf")
        sys.exit(1)

    inp = sys.argv[1]
    if not os.path.exists(inp):
        print("Not found:", inp); sys.exit(1)

    print("1️⃣ Uploading…")
    up_url = upload_file(inp)

    base,name = os.path.splitext(os.path.basename(inp))
    searchable_name = f"{base}_searchable.pdf"

    print("2️⃣ OCR→ searchable PDF…")
    searchable_url = make_searchable(up_url, searchable_name)

    print("3️⃣ Downloading searchable PDF…")
    download_file(searchable_url, searchable_name)
    print("   Saved:", searchable_name)

    print("4️⃣ Extracting tables via JSON2…")
    body = extract_to_json2(searchable_url)

    print("5️⃣ Parsing tables…")
    tables = parse_json2_tables(body)

    out = "parsed_tables.json"
    with open(out,"w") as f:
        json.dump(tables, f, indent=4)
    print("✅ Parsed tables written to", out)

if __name__=="__main__":
    main()
