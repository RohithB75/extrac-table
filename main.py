import os
import re
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


def normalize_headers(headers, fallback_width=0):
    if isinstance(headers, list):
        cleaned = [str(h).strip() for h in headers if str(h).strip()]
    elif isinstance(headers, str):
        cleaned = [part.strip() for part in re.split(r",+", headers) if part.strip()]
        if len(cleaned) <= 1:
            cleaned = [part.strip() for part in re.split(r"\s{2,}|\n+", headers) if part.strip()]
    else:
        cleaned = []

    if fallback_width and len(cleaned) < fallback_width:
        cleaned.extend([f"Column {i + 1}" for i in range(len(cleaned), fallback_width)])

    return cleaned


def _iter_ocr_lines(node):
    if not node:
        return

    if (
        isinstance(node, list)
        and len(node) == 2
        and isinstance(node[0], (list, tuple))
        and isinstance(node[1], (list, tuple))
        and node[1]
        and isinstance(node[1][0], str)
    ):
        yield node
        return

    if isinstance(node, list):
        for child in node:
            yield from _iter_ocr_lines(child)


def _words_to_rows(words):
    clean_words = [word for word in words if word.get("text")]
    if not clean_words:
        return []

    clean_words.sort(key=lambda item: (item["y"], item["x"]))
    heights = [max(1, word["y1"] - word["y0"]) for word in clean_words]
    row_tolerance = max(10, int(np.median(heights) * 0.8)) if heights else 10

    row_groups = []
    current_group = []
    current_y = None
    for word in clean_words:
        if current_y is None or abs(word["y"] - current_y) <= row_tolerance:
            current_group.append(word)
            current_y = word["y"] if current_y is None else (current_y + word["y"]) / 2
        else:
            row_groups.append(sorted(current_group, key=lambda item: item["x"]))
            current_group = [word]
            current_y = word["y"]
    if current_group:
        row_groups.append(sorted(current_group, key=lambda item: item["x"]))

    rows = []
    for row in row_groups:
        page_width = max((word["x1"] for word in row), default=0)
        gap_threshold = max(24, int(page_width * 0.045)) if page_width else 24
        cells = []
        current_cell = [row[0]]
        current_right = row[0]["x1"]

        for word in row[1:]:
            if word["x0"] - current_right > gap_threshold:
                cells.append(" ".join(item["text"] for item in current_cell).strip())
                current_cell = [word]
            else:
                current_cell.append(word)
            current_right = max(current_right, word["x1"])

        if current_cell:
            cells.append(" ".join(item["text"] for item in current_cell).strip())

        if any(cell for cell in cells):
            rows.append(cells)

    return rows


def ocr_to_rows(ocr_data):
    words = []
    for line in _iter_ocr_lines(ocr_data):
        box = line[0]
        text_info = line[1]
        text = str(text_info[0]).strip() if text_info and text_info[0] else ""
        if not text:
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        words.append(
            {
                "text": text,
                "x": min(xs),
                "y": sum(ys) / len(ys),
                "x0": min(xs),
                "x1": max(xs),
                "y0": min(ys),
                "y1": max(ys),
            }
        )

    return _words_to_rows(words)


def tesseract_to_rows(image):
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    words = []

    for index, text in enumerate(data.get("text", [])):
        text = str(text).strip()
        if not text:
            continue

        try:
            confidence = float(data.get("conf", [])[index])
        except Exception:
            confidence = -1

        if confidence < 0:
            continue

        left = int(data.get("left", [0])[index])
        top = int(data.get("top", [0])[index])
        width = int(data.get("width", [0])[index])
        height = int(data.get("height", [0])[index])

        words.append(
            {
                "text": text,
                "x": left,
                "y": top + height / 2,
                "x0": left,
                "x1": left + width,
                "y0": top,
                "y1": top + height,
            }
        )

    return _words_to_rows(words)


def score_header_row(row):
    text = " ".join(str(cell).strip().lower() for cell in row if str(cell).strip())
    if not text:
        return -1

    keywords = [
        "carton",
        "order",
        "part number",
        "description",
        "unit price",
        "qty",
        "quantity",
        "sales value",
        "customs value",
    ]
    score = len(row) * 2
    for keyword in keywords:
        if keyword in text:
            score += 5

    if any(marker in text for marker in ["invoice", "dated", "time:", "commercial invoice"]):
        score -= 4

    if sum(char.isdigit() for char in text) > len(text) * 0.2:
        score -= 2

    return score


def select_header_row(rows):
    best_index = 0
    best_score = -1
    for index, row in enumerate(rows):
        score = score_header_row(row)
        if score > best_score:
            best_index = index
            best_score = score
    return best_index


def is_continuation_row(row):
    nonempty = [str(cell).strip() for cell in row if str(cell).strip()]
    if not nonempty:
        return True

    text = " ".join(nonempty).strip()
    lower = text.lower()
    prefixes = (
        "country of origin:",
        "commodity code",
        "continued",
        "page ",
        "d:",
        "invoice",
        "commercial invoice",
    )

    if lower.startswith(prefixes):
        return True

    if len(nonempty) == 1 and len(text) <= 90:
        return True

    footer_markers = (
        "page ",
        "continued...",
        "company details:",
        "commercial.rpt",
        "d: ",
    )
    if any(marker in lower for marker in footer_markers):
        return True

    if len(nonempty) == 2 and any(token in lower for token in ["country of origin", "commodity code"]):
        return True

    return False


def merge_continuation_rows(rows):
    merged = []
    for row in rows:
        if is_continuation_row(row) and merged:
            previous = merged[-1][:]
            text = " ".join(str(cell).strip() for cell in row if str(cell).strip()).strip()
            if text:
                target_index = 1 if len(previous) > 1 else 0
                if any(keyword in text.lower() for keyword in ["page ", "continued...", "commercial.rpt", "company details:", "d: "]):
                    continue
                if previous[target_index]:
                    previous[target_index] = f"{previous[target_index]} {text}".strip()
                else:
                    previous[target_index] = text
                merged[-1] = previous
            continue

        merged.append(list(row))

    return merged


def clean_scanned_row(row, header_count):
    values = ["" if cell is None else str(cell).strip() for cell in row]
    if not values:
        return []

    if len(values) > header_count:
        values = values[: header_count - 1] + [" ".join(values[header_count - 1 :]).strip()]

    cleaned = []
    for index, cell in enumerate(values):
        text = cell.strip()
        lower = text.lower()

        if not text:
            cleaned.append("")
            continue

        if any(marker in lower for marker in ["continued...", "commercial.rpt", "page ", "d: "]):
            continue

        if index == 1:
            for marker in ["country of origin:", "commodity code", "export cpc"]:
                marker_index = lower.find(marker)
                if marker_index != -1:
                    text = text[:marker_index].strip()
                    lower = text.lower()

        text = re.sub(r"\s+", " ", text).strip(" -|()	")
        cleaned.append(text)

    while len(cleaned) < header_count:
        cleaned.append("")

    return cleaned[:header_count]


def clean_scanned_rows(rows, header_count):
    cleaned_rows = []
    for row in rows:
        cleaned = clean_scanned_row(row, header_count)
        if cleaned and any(cell for cell in cleaned):
            cleaned_rows.append(cleaned)
    return cleaned_rows

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

        raw_rows = df.iloc[1:].astype(str).fillna("").values.tolist()  # All rows below header
        headers = normalize_headers(df.iloc[0].tolist(), fallback_width=max((len(row) for row in raw_rows), default=0))

        if len(headers) < max((len(row) for row in raw_rows), default=0):
            headers = [f"Column {i + 1}" for i in range(max((len(row) for row in raw_rows), default=0))]

        tables.append({
            'page': tbl.page,
            'headers': headers,  # Preserve headers as a list for column-aligned output
            'raw_rows': raw_rows,    # Raw rows
            'processed_rows': raw_rows,  # Processed rows (same as raw for now)
            'metadata': tbl.parsing_report
        })

    return tables


def looks_poor_table(table):
    headers = table.get('headers', [])
    rows = table.get('raw_rows', [])

    if isinstance(headers, str):
        headers = [headers]

    if len(headers) <= 1:
        return True

    sample_rows = [row for row in rows if isinstance(row, list)][:5]
    if not sample_rows:
        return True

    one_cell_rows = sum(1 for row in sample_rows if len(row) <= 1)
    if one_cell_rows >= max(1, len(sample_rows) - 1):
        return True

    very_long_flat_rows = sum(1 for row in sample_rows if len(row) == 1 and len(str(row[0])) > 80)
    if very_long_flat_rows:
        return True

    return False

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
        try:
            if hasattr(ocr, "predict"):
                ocr_data = ocr.predict(arr)
            else:
                ocr_data = ocr.ocr(arr)
        except Exception as exc:
            print(f"PaddleOCR failed on page {p}: {exc}. Falling back to Tesseract.")
            ocr_data = []
        rows_all = ocr_to_rows(ocr_data)

        # Fallback to Tesseract if PaddleOCR fails
        if not rows_all:
            img = Image.fromarray(arr)
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            words = []
            for idx, text in enumerate(data.get("text", [])):
                text = str(text).strip()
                if not text:
                    continue
                conf = data.get("conf", [])[idx]
                try:
                    conf_value = float(conf)
                except Exception:
                    conf_value = -1
                if conf_value < 0:
                    continue
                words.append({
                    "text": text,
                    "x": int(data.get("left", [0])[idx]),
                    "y": int(data.get("top", [0])[idx]),
                })

            words.sort(key=lambda item: (round(item["y"] / 12), item["x"]))
            grouped = []
            current_row = []
            current_y = None
            for word in words:
                if current_y is None or abs(word["y"] - current_y) <= 12:
                    current_row.append(word)
                    current_y = word["y"] if current_y is None else (current_y + word["y"]) / 2
                else:
                    grouped.append(sorted(current_row, key=lambda value: value["x"]))
                    current_row = [word]
                    current_y = word["y"]
            if current_row:
                grouped.append(sorted(current_row, key=lambda value: value["x"]))

            rows_all = [[word["text"] for word in row] for row in grouped]

        header_index = select_header_row(rows_all) if rows_all else 0
        headers = normalize_headers(
            rows_all[header_index] if rows_all else [],
            fallback_width=max((len(row) for row in rows_all[header_index + 1 :]), default=0),
        )
        data_rows = rows_all[header_index + 1:] if len(rows_all) > header_index + 1 else []
        data_rows = merge_continuation_rows(data_rows)
        data_rows = clean_scanned_rows(data_rows, len(headers))
        data_rows = [row + [""] * (len(headers) - len(row)) for row in data_rows]

        title_rows = rows_all[:header_index]
        title = " ".join(
            " ".join(str(cell).strip() for cell in row if str(cell).strip()) for row in title_rows
        ).strip()
        if not title:
            title = " ".join(headers) if headers else ""
        tables.append({
            'page': p,
            'title': title,
            'headers': headers,
            'rows': data_rows,
            'metadata': {'ocr_method': 'PaddleOCR' if rows_all and isinstance(ocr_data, list) else 'Tesseract'}
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
            if tables and any(looks_poor_table(table) for table in tables):
                print(f"Camelot output looks flattened for {pdf_path}. Falling back to OCR reconstruction.")
                tables = extract_tables_scanned(pdf_path, pages, ocr)

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
    try:
        ocr = PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, lang='en')
    except TypeError:
        ocr = PaddleOCR(use_angle_cls=False, lang='en')
    results = Parallel(n_jobs=parallel)(delayed(process_pdf)(pdf, pages_list, output_format, ocr) for pdf in pdfs)
    for r in results:
        click.echo(f"Generated: {r}")

if __name__ == '__main__':
    main()
