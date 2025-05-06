# PDF Table Extractor

A powerful Python tool for extracting tables from PDF documents, supporting both digital and scanned PDFs.

## Features

- Extract tables from both digital and scanned PDFs
- Support for multiple extraction methods (Camelot, PaddleOCR)
- Output in JSON or Markdown format
- Batch processing with parallel execution
- Page-specific extraction
- GPU acceleration support (optional)

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  PDF Input  │────▶│  Extraction │────▶│   Output    │
│  (Digital/  │     │   Engine    │     │  (JSON/MD)  │
│   Scanned)  │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘
```

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/pdf-table-extractor.git
cd pdf-table-extractor
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. (Optional) Install Tesseract OCR:
- Windows: Download and install from https://github.com/UB-Mannheim/tesseract/wiki
- Linux: `sudo apt-get install tesseract-ocr`
- macOS: `brew install tesseract`

## Usage

### Basic Usage

```bash
python cli.py path/to/your.pdf
```

### Advanced Options

```bash
python cli.py path/to/your.pdf \
    --pages "1,3,5-7" \
    --output-dir output \
    --method auto \
    --format json \
    --parallel
```

### Options

- `--pages`, `-p`: Page numbers to process (e.g., "1,3,5-7" or "all")
- `--output-dir`, `-o`: Output directory (default: "output")
- `--method`, `-m`: Extraction method (auto, camelot, paddle)
- `--format`, `-f`: Output format (json, markdown)
- `--parallel/--no-parallel`: Enable/disable parallel processing

### Environment Variables

Create a `.env` file in the project root:

```env
USE_GPU=true  # Enable GPU acceleration for PaddleOCR
```

## Implementation Details

### Tools and Libraries

1. PDF Processing:
   - PyMuPDF: For basic PDF operations and text extraction
   - Camelot: For structured table extraction from digital PDFs
   - PaddleOCR: For OCR and table detection in scanned PDFs

2. Data Processing:
   - pandas: For table data manipulation
   - numpy: For numerical operations
   - OpenCV: For image processing and table detection

3. Output Formatting:
   - tabulate: For Markdown table generation
   - json: For JSON output

4. Parallel Processing:
   - joblib: For parallel file processing
   - click: For CLI interface

### Tool Choices Rationale

1. **Camelot**: Best for digital PDFs with clear table structures
2. **PaddleOCR**: Superior accuracy for scanned documents and complex layouts
3. **PyMuPDF**: Fast and reliable for basic PDF operations
4. **OpenCV**: Industry standard for image processing and table detection

## Security

- No hardcoded credentials
- Environment variables for configuration
- Input validation and sanitization
- Error handling and logging

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

This project is licensed under the Apache License 2.0 - see the LICENSE file for details.

## Acknowledgments

- Camelot-py team for the excellent table extraction library
- PaddleOCR team for the powerful OCR engine
- All contributors and users of this project 
