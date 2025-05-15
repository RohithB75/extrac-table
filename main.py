import os
import fitz  # PyMuPDF
import camelot
import pytesseract
from paddleocr import PaddleOCR
from PIL import Image
import numpy as np
import click
from joblib import Parallel, delayed
import json
import pandas as pd
from tabulate import tabulate

# Add a check to validate the page numbers before processing:
def validate_pages(pdf_path, pages):
    doc = fitz.open(pdf_path)
    total_pages = doc.page_count
    return [p for p in pages if 1 <= p <= total_pages]

# Function to parse pages (range like 1-3,5)
def parse_pages(pages_str):
    pages = set()
    for part in pages_str.split(','):
        part = part.strip()
        if '-' in part:  # Handle ranges like 1-3
            start, end = map(int, part.split('-'))
            pages.update(range(start, end + 1))
        else:  # Handle single pages like 5
            pages.add(int(part))
    return sorted(pages)

# Function to detect if PDF is scanned (based on text extraction)
def is_scanned(pdf_path, page_no=0):
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_no)
    text = page.get_text("text").strip()
    return len(text) < 20  # Consider scanned if no or little text

# Extract tables from digital PDFs (Camelot)
def extract_tables_digital(pdf_path, pages):
    doc = fitz.open(pdf_path)
    total_pages = doc.page_count
    pages = [p for p in pages if 1 <= p <= total_pages]  # Validate pages

    if not pages:
        raise ValueError(f"No valid pages to process in {pdf_path}")

    tables = []
    page_str = ",".join(map(str, pages))

    # Try flavor="lattice" (for grid-like tables)
    camelot_tables = camelot.read_pdf(pdf_path, pages=page_str, flavor="lattice", strip_text="\n")
    
    if not camelot_tables:
        print(f"No tables found with lattice on {pdf_path}")
        # Try flavor="stream" (for non-grid text-based tables)
        camelot_tables = camelot.read_pdf(pdf_path, pages=page_str, flavor="stream", strip_text="\n")

    for tbl in camelot_tables:
        df = tbl.df
        if df.empty:
            print(f"No valid data found on page {tbl.page}")  # Debug print for empty tables
            continue

        # Extract headers
        headers = df.iloc[0].tolist()  # First row as headers
        raw_rows = df.iloc[1:].values.tolist()  # All rows below header

        # Clean headers
        headers = [header.strip().replace("\n", " ").replace(",", "") for header in headers]  # Clean headers
        headers_str = ",".join(headers)  # Join headers as a comma-separated string

        tables.append({
            'page': tbl.page,
            'headers': headers_str,  # Save headers as a comma-separated string
            'raw_rows': raw_rows,    # Raw rows
            'processed_rows': raw_rows,  # Processed rows (same as raw for now)
            'metadata': tbl.parsing_report
        })

    return tables

# Extract tables from scanned PDFs using PaddleOCR + Tesseract fallback
def extract_tables_scanned(pdf_path, pages, ocr):
    tables = []
    doc = fitz.open(pdf_path)

    for p in pages:
        if p < 1 or p > doc.page_count:
            continue
        page = doc.load_page(p - 1)
        pix = page.get_pixmap(dpi=300)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)

        # Try PaddleOCR
        ocr_data = ocr.ocr(arr, cls=True)
        lines = []
        if isinstance(ocr_data, list):
            for block in ocr_data:
                if not block:
                    continue
                for line in block:
                    text = line[1][0].strip()
                    if text:
                        lines.append(text)

        # Fallback to Tesseract if PaddleOCR fails
        if not lines:
            img = Image.fromarray(arr)
            text = pytesseract.image_to_string(img)
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        # Structure into headers and rows
        rows_all = [ln.split() for ln in lines]
        headers = rows_all[0] if rows_all else []
        data_rows = rows_all[1:] if len(rows_all) > 1 else []

        headers = [h.strip() for h in headers]
        data_rows = [row[:len(headers)] for row in data_rows]  # Align rows to headers

        title = lines[0] if lines else ""
        tables.append({
            'page': p,
            'title': title,
            'headers': headers,
            'rows': data_rows,
            'metadata': {'ocr_method': 'PaddleOCR' if lines and isinstance(ocr_data, list) else 'Tesseract'}
        })

    return tables

# Process a single PDF
import tempfile
import shutil

def process_pdf(pdf_path, pages, output_format, ocr):
    tempdir = tempfile.mkdtemp()  # Create a unique temporary directory
    try:
        scanned = is_scanned(pdf_path, page_no=pages[0] - 1)
        if scanned:
            tables = extract_tables_scanned(pdf_path, pages, ocr)
        else:
            tables = extract_tables_digital(pdf_path, pages)

        if not pages:
            raise ValueError(f"No valid pages to process in {pdf_path}")

        base = os.path.splitext(os.path.basename(pdf_path))[0]
        out_file = f"{base}_tables.{ 'json' if output_format=='json' else 'md' }"

        if output_format == 'json':
            with open(out_file, 'w') as f:
                json.dump(tables, f, indent=4)
        else:
            md = ""
            for tbl in tables:
                md += f"### Page {tbl['page']} — {tbl['title']}\n\n"
                df = pd.DataFrame(tbl['rows'], columns=tbl['headers'])
                md += tabulate(df, headers='keys', tablefmt='github') + "\n\n"
            with open(out_file, 'w') as f:
                f.write(md)

        return out_file
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)  # Clean up temporary directory

@click.command()
@click.option('--input-dir', '-i', type=click.Path(exists=True), required=True, help='Directory with PDF files')
@click.option('--pages', '-p', required=True, help='Comma-separated pages/ranges (e.g., "1-3")')
@click.option('--format', '-f', 'output_format', type=click.Choice(['json','markdown']), default='json', help='Output format')
@click.option('--parallel', '-n', default=1, help='Number of parallel jobs')
def main(input_dir, pages, output_format, parallel):
    pages_list = parse_pages(pages)
    pdfs = [os.path.join(input_dir, f) for f in os.listdir(input_dir) if f.lower().endswith('.pdf')]
    ocr = PaddleOCR(use_angle_cls=True, lang='en')
    results = Parallel(n_jobs=parallel)(delayed(process_pdf)(pdf, pages_list, output_format, ocr) for pdf in pdfs)
    for r in results:
        click.echo(f"Generated: {r}")

if __name__ == '__main__':
    main()
