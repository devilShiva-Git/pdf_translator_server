import os
import io
import fitz  # PyMuPDF
import requests
from flask import Flask, request, send_file, jsonify

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB

FONT_FILE = os.path.join(os.path.dirname(__file__), "NotoSansDevanagari-Regular.ttf")
LT_URL = os.environ.get("LT_URL", "https://libretranslate.com/translate")

# Startup logging
print("=" * 70)
print("🚀 PDF Translator Server Starting...")
print(f"🌐 Translation API: {LT_URL}")
print(f"🔤 Font file: {FONT_FILE}")
print(f"   Font exists: {'✅ YES' if os.path.exists(FONT_FILE) else '❌ NO'}")
print("=" * 70)


def translate_texts(texts, target_lang="hi", source_lang="en"):
    """Translate a list of strings using LibreTranslate."""
    translated = []

    for idx, text in enumerate(texts):
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
                result = response.json().get("translatedText", text)

                # Log first 3 translations to see what's happening
                if idx < 3:
                    original_preview = text[:50] + "..." if len(text) > 50 else text
                    translated_preview = result[:50] + "..." if len(result) > 50 else result
                    print(f"   [TRANSLATE {idx + 1}] '{original_preview}'")
                    print(f"                    → '{translated_preview}'")

                translated.append(result)
            else:
                print(f"   ❌ API Error {response.status_code}: {response.text[:100]}")
                translated.append(text)

        except Exception as e:
            print(f"   ❌ Exception: {type(e).__name__}: {str(e)[:100]}")
            translated.append(text)

    return translated


@app.route("/")
def index():
    """Health check endpoint."""
    return jsonify({
        "status": "running",
        "service": "PDF Translator",
        "translation_api": LT_URL,
        "font_available": os.path.exists(FONT_FILE),
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
    file = request.files.get("file")
    if not file:
        print("❌ No file uploaded")
        return jsonify({"error": "No file uploaded"}), 400

    target_lang = request.form.get("target", "hi")
    source_lang = request.form.get("source", "en")

    print("\n" + "=" * 70)
    print(f"📄 NEW TRANSLATION REQUEST")
    print(f"   File: {file.filename}")
    print(f"   Direction: {source_lang.upper()} → {target_lang.upper()}")
    print("=" * 70)

    try:
        src_pdf = fitz.open(stream=file.read(), filetype="pdf")
        out_pdf = fitz.open()

        total_pages = len(src_pdf)
        total_spans = 0

        print(f"📖 Processing {total_pages} pages...")

        for page_num in range(total_pages):
            src_page = src_pdf[page_num]
            rect = src_page.rect

            out_page = out_pdf.new_page(width=rect.width, height=rect.height)
            out_page.show_pdf_page(rect, src_pdf, page_num)

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
                print(f"   Page {page_num + 1}: No text found (might be image-only)")
                continue

            total_spans += len(spans)
            print(f"   Page {page_num + 1}: Found {len(spans)} text elements")

            # Translate
            texts = [span[1] for span in spans]
            translated_texts = translate_texts(
                texts,
                target_lang=target_lang,
                source_lang=source_lang
            )

            # Cover original text
            for bbox, _, _ in spans:
                rect_obj = fitz.Rect(bbox)
                out_page.draw_rect(rect_obj, color=None, fill=(1, 1, 1), overlay=True)

            # Insert translated text
            fontfile = FONT_FILE if os.path.exists(FONT_FILE) else None

            if not fontfile and page_num == 0:
                print(f"   ⚠️  Warning: Font file not found, using default font")

            for idx, (bbox, _, size) in enumerate(spans):
                rect_obj = fitz.Rect(bbox)
                fontsize = max(8, min(int(size), 18))

                leftover = out_page.insert_textbox(
                    rect_obj,
                    translated_texts[idx],
                    fontfile=fontfile,
                    fontsize=fontsize,
                    color=(0, 0, 0),
                    align=0,
                    render_mode=0,
                )

                while leftover and fontsize > 8:
                    fontsize -= 1
                    out_page.draw_rect(rect_obj, color=None, fill=(1, 1, 1), overlay=True)
                    leftover = out_page.insert_textbox(
                        rect_obj,
                        translated_texts[idx],
                        fontfile=fontfile,
                        fontsize=fontsize,
                        color=(0, 0, 0),
                        align=0,
                        render_mode=0,
                    )

            out_page.clean_contents()

        # Generate output
        out_bytes = io.BytesIO()
        out_pdf.save(out_bytes)
        out_bytes.seek(0)

        output_size = len(out_bytes.getvalue())

        print(f"✅ SUCCESS!")
        print(f"   Total text elements translated: {total_spans}")
        print(f"   Output PDF size: {output_size // 1024}KB")
        print("=" * 70 + "\n")

        return send_file(
            out_bytes,
            mimetype="application/pdf",
            as_attachment=False,
            download_name=f"translated_{target_lang}.pdf"
        )

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"❌ TRANSLATION FAILED: {error_msg}")
        print("=" * 70 + "\n")
        return jsonify({"error": error_msg}), 500

    finally:
        if 'src_pdf' in locals():
            src_pdf.close()
        if 'out_pdf' in locals():
            out_pdf.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)