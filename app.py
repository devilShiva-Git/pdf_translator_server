import os
import io
import fitz  # PyMuPDF
import requests
from flask import Flask, request, send_file, jsonify

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB upload limit

# Font file path - will be in the same folder as app.py
FONT_FILE = os.path.join(os.path.dirname(__file__), "NotoSansDevanagari-Regular.ttf")

# LibreTranslate public endpoint (can be overridden via env var)
LT_URL = os.environ.get("LT_URL", "https://libretranslate.com/translate")


def translate_texts(texts, target_lang="hi", source_lang="en"):
    """Translate a list of strings using LibreTranslate."""
    translated = []
    for text in texts:
        if not text.strip():
            translated.append(text)
            continue

        payload = {
            "q": text,
            "source": source_lang,
            "target": target_lang,
            "format": "text",
        }

        try:
            response = requests.post(LT_URL, json=payload, timeout=30)
            if response.ok:
                translated.append(response.json().get("translatedText", text))
            else:
                translated.append(text)
        except Exception as e:
            print(f"Translation error: {e}")
            translated.append(text)

    return translated


@app.route("/")
def index():
    """Health check endpoint."""
    return jsonify({
        "status": "running",
        "service": "PDF Translator",
        "endpoints": {
            "/": "Health check",
            "/translate-pdf": "POST - Upload PDF for translation"
        }
    })


@app.post("/translate-pdf")
def translate_pdf():
    """
    Translate PDF endpoint.
    Form-data:
      - file: the PDF file
      - target: target language (default "hi")
      - source: source language (default "en")
    """
    # Get uploaded file
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file uploaded"}), 400

    # Get language parameters
    target_lang = request.form.get("target", "hi")
    source_lang = request.form.get("source", "en")

    try:
        # Open the PDF
        src_pdf = fitz.open(stream=file.read(), filetype="pdf")
        out_pdf = fitz.open()

        # Process each page
        for page_num in range(len(src_pdf)):
            src_page = src_pdf[page_num]
            rect = src_page.rect

            # Create new page with same dimensions
            out_page = out_pdf.new_page(width=rect.width, height=rect.height)

            # Copy the original page content (images, vectors, etc.)
            out_page.show_pdf_page(rect, src_pdf, page_num)

            # Extract text spans with positions
            raw_dict = src_page.get_text("rawdict")
            spans = []

            for block in raw_dict.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = (span.get("text") or "").strip()
                        if text:
                            bbox = span.get("bbox")
                            size = span.get("size", 12)
                            spans.append((bbox, text, size))

            if not spans:
                continue

            # Translate all texts
            texts = [span[1] for span in spans]
            translated_texts = translate_texts(texts, target_lang=target_lang, source_lang=source_lang)

            # Cover original text with white rectangles
            for bbox, _, _ in spans:
                rect = fitz.Rect(bbox)
                out_page.draw_rect(rect, color=None, fill=(1, 1, 1), overlay=True)

            # Insert translated text
            for idx, (bbox, _, size) in enumerate(spans):
                rect = fitz.Rect(bbox)
                fontsize = max(8, min(int(size), 18))

                # Check if font file exists
                fontfile = FONT_FILE if os.path.exists(FONT_FILE) else None

                # Try to insert text, shrinking font if needed
                leftover = out_page.insert_textbox(
                    rect,
                    translated_texts[idx],
                    fontfile=fontfile,
                    fontsize=fontsize,
                    color=(0, 0, 0),
                    align=0,
                    render_mode=0,
                )

                # Shrink font if text doesn't fit
                while leftover and fontsize > 8:
                    fontsize -= 1
                    out_page.draw_rect(rect, color=None, fill=(1, 1, 1), overlay=True)
                    leftover = out_page.insert_textbox(
                        rect,
                        translated_texts[idx],
                        fontfile=fontfile,
                        fontsize=fontsize,
                        color=(0, 0, 0),
                        align=0,
                        render_mode=0,
                    )

            out_page.clean_contents()

        # Save and return the translated PDF
        out_bytes = io.BytesIO()
        out_pdf.save(out_bytes)
        out_bytes.seek(0)

        return send_file(
            out_bytes,
            mimetype="application/pdf",
            as_attachment=False,
            download_name="translated.pdf"
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        if 'src_pdf' in locals():
            src_pdf.close()
        if 'out_pdf' in locals():
            out_pdf.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)