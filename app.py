import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="PDF Table Viewer",
    page_icon="📄",
    layout="wide",
)


st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.25rem;
        padding-bottom: 2rem;
        max-width: 1400px;
    }
    .hero {
        background: linear-gradient(135deg, #0f172a 0%, #1e293b 45%, #334155 100%);
        color: white;
        padding: 1.5rem 1.75rem;
        border-radius: 1.25rem;
        margin-bottom: 1rem;
        box-shadow: 0 12px 40px rgba(15, 23, 42, 0.18);
    }
    .hero h1 {
        margin: 0;
        font-size: 2rem;
        line-height: 1.1;
    }
    .hero p {
        margin: 0.45rem 0 0;
        opacity: 0.88;
        font-size: 0.98rem;
    }
    .metric-card {
        background: white;
        border: 1px solid rgba(148, 163, 184, 0.22);
        border-radius: 1rem;
        padding: 1rem 1.1rem;
        box-shadow: 0 10px 30px rgba(15, 23, 42, 0.04);
    }
    .section-title {
        font-size: 1.05rem;
        font-weight: 700;
        margin: 0.2rem 0 0.35rem;
    }
    .muted {
        color: #64748b;
        font-size: 0.9rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def load_json(source: Path) -> list[dict]:
    with source.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("tables", []) if isinstance(payload.get("tables", []), list) else []
    return []


def split_headers(headers_value: object, row_count: int | None = None) -> list[str]:
    if isinstance(headers_value, list):
        headers = [str(item).strip() for item in headers_value if str(item).strip()]
    elif isinstance(headers_value, str):
        headers = [part.strip() for part in re.split(r",+", headers_value) if part.strip()]
        if len(headers) <= 1:
            headers = [part.strip() for part in re.split(r"\s{2,}|\n+", headers_value) if part.strip()]
    else:
        headers = []

    if row_count and headers and len(headers) > row_count:
        headers = headers[:row_count]

    if not headers:
        count = row_count or 0
        headers = [f"Column {idx + 1}" for idx in range(count)]

    return headers


def rows_to_dataframe(table: dict) -> pd.DataFrame:
    rows = table.get("processed_rows") or table.get("raw_rows") or []
    normalized_rows = []
    max_width = 0
    for row in rows:
        if isinstance(row, list):
            values = ["" if value is None else str(value) for value in row]
        else:
            values = [str(row)]
        max_width = max(max_width, len(values))
        normalized_rows.append(values)

    headers = split_headers(table.get("headers"), max_width or None)
    if len(headers) < max_width:
        headers.extend([f"Column {idx + 1}" for idx in range(len(headers), max_width)])

    padded_rows = [values + [""] * (len(headers) - len(values)) for values in normalized_rows]
    if not padded_rows:
        return pd.DataFrame(columns=headers)
    return pd.DataFrame(padded_rows, columns=headers)


def summarize_tables(tables: list[dict]) -> tuple[int, int, list[int]]:
    pages = sorted({int(table.get("page", 0)) for table in tables if str(table.get("page", "")).isdigit()})
    return len(tables), len(pages), pages


st.markdown(
    """
    <div class="hero">
        <h1>PDF Table Viewer</h1>
        <p>Load extracted table output and inspect each page in a cleaner, filterable layout.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

left_col, right_col = st.columns([1.1, 1.9], gap="large")

with left_col:
    st.markdown('<div class="section-title">Data Source</div>', unsafe_allow_html=True)
    uploaded_file = st.file_uploader("Upload a JSON output file", type=["json"])

    default_path = Path("Document2_tables.json")
    if uploaded_file is None and default_path.exists():
        st.caption(f"Using local file: {default_path.name}")
        source_path = default_path
    else:
        source_path = None

    if uploaded_file is not None:
        tables = json.loads(uploaded_file.read().decode("utf-8"))
        if isinstance(tables, dict):
            tables = tables.get("tables", [])
        source_name = uploaded_file.name
    elif source_path is not None:
        tables = load_json(source_path)
        source_name = source_path.name
    else:
        tables = []
        source_name = "No file loaded"

    total_tables, total_pages, pages = summarize_tables(tables)

    st.markdown(
        f"""
        <div class="metric-card">
            <div class="muted">Source</div>
            <div class="section-title">{source_name}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    metric_col_1, metric_col_2 = st.columns(2)
    with metric_col_1:
        st.metric("Tables", total_tables)
    with metric_col_2:
        st.metric("Pages", total_pages)

    if pages:
        page_filter = st.multiselect("Filter pages", pages, default=pages)
    else:
        page_filter = []

    show_metadata = st.checkbox("Show metadata", value=True)
    show_raw_json = st.checkbox("Show raw JSON", value=False)
    search_text = st.text_input("Search within tables", placeholder="Type a page number, header, or text fragment")

with right_col:
    if not tables:
        st.info("Upload a JSON file or keep `Document2_tables.json` in the project root to start viewing tables.")
    else:
        filtered_tables = []
        for index, table in enumerate(tables, start=1):
            page_value = table.get("page")
            page_match = not page_filter or page_value in page_filter
            haystack = " ".join(
                [
                    str(page_value),
                    str(table.get("headers", "")),
                    json.dumps(table.get("metadata", {}), ensure_ascii=False),
                    json.dumps(table.get("raw_rows", []), ensure_ascii=False),
                ]
            ).lower()
            search_match = not search_text or search_text.lower() in haystack
            if page_match and search_match:
                filtered_tables.append((index, table))

        st.caption(f"Showing {len(filtered_tables)} of {len(tables)} extracted table entries.")

        if not filtered_tables:
            st.warning("No tables matched the current filters.")
        else:
            for index, table in filtered_tables:
                page_value = table.get("page", "Unknown")
                title = table.get("title") or f"Table {index}"
                metadata = table.get("metadata", {})
                df = rows_to_dataframe(table)

                with st.expander(f"Page {page_value} - {title}", expanded=index == filtered_tables[0][0]):
                    top_col_1, top_col_2, top_col_3 = st.columns(3)
                    top_col_1.metric("Rows", len(df))
                    top_col_2.metric("Columns", len(df.columns))
                    top_col_3.metric("Page", page_value)

                    st.dataframe(df, use_container_width=True, hide_index=True)

                    download_name = f"page_{page_value}_table_{index}.csv"
                    st.download_button(
                        label="Download CSV",
                        data=df.to_csv(index=False).encode("utf-8"),
                        file_name=download_name,
                        mime="text/csv",
                    )

                    if show_metadata:
                        st.markdown('<div class="section-title">Metadata</div>', unsafe_allow_html=True)
                        st.json(metadata)

                    if show_raw_json:
                        st.markdown('<div class="section-title">Raw Entry</div>', unsafe_allow_html=True)
                        st.json(table)


st.caption("Tip: if the extracted headers are messy, the viewer auto-generates column names so the table still renders cleanly.")
