import os
import io
import fitz  # PyMuPDF
import requests
import time
from flask import Flask, request, send_file, jsonify

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB

FONT_FILE = os.path.join(os.path.dirname(__file__), "NotoSansDevanagari-Regular.ttf")

# MyMemory Translation API (free, no key needed)
MYMEMORY_URL = "https://api.mymemory.translated.net/get"

print("=" * 70)
print("🚀 PDF Translator Server Starting...")
print(f"🌐 Translation API: MyMemory (free)")
print(f"🔤 Font file: {FONT_FILE}")
print(f"   Font exists: {'✅ YES' if os.path.exists(FONT_FILE) else '❌ NO'}")
print("=" * 70)


def translate_text_mymemory(text, target_lang="hi", source_lang="en"):
    """Translate text using MyMemory API."""
    try:
        # MyMemory uses ISO language codes
        lang_pair = f"{source_lang}|{target_lang}"

        params = {
            "q": text[:500],  # MyMemory limit is 500 chars per request
            "langpair": lang_pair
        }

        response = requests.get(MYMEMORY_URL, params=params, timeout=10)

        if response.ok:
            data = response.json()
            if data.get("responseStatus") == 200:
                return data.get("responseData", {}).get("translatedText", text)

        return text
    except Exception as e:
        print(f"   ⚠️  Translation error: {e}")
        return text


def translate_texts_batch(texts, target_lang="hi", source_lang="en"):
    """Translate a list of texts with rate limiting."""
    translated = []
    total = len(texts)

    print(f"   🔄 Translating {total} text elements...")

    for idx, text in enumerate(texts):
        if not text.strip():
            translated.append(text)
            continue

        # Translate
        result = translate_text_mymemory(text, target_lang, source_lang)
        translated.append(result)

        # Log first 3 translations
        if idx < 3:
            original_preview = text[:40] + "..." if len(text) > 40 else text
            translated_preview = result[:40] + "..." if len(result) > 40 else result
            print(f"      [{idx + 1}] '{original_preview}'")
            print(f"          → '{translated_preview}'")

        # Progress indicator every 25 items
        if (idx + 1) % 25 == 0:
            print(f"   📊 Progress: {idx + 1}/{total}")

        # Small delay to avoid rate limiting (MyMemory allows ~10 req/sec)
        if idx < total - 1:
            time.sleep(0.15)

    return translated


def extract_text_blocks(page):
    """Extract text blocks from a page using multiple methods."""
    text_blocks = []

    # Method 1: Try dict format (structured extraction)
    try:
        blocks_dict = page.get_text("dict")
        for block in blocks_dict.get("blocks", []):
            if block.get("type") == 0:  # Text block
                for line in block.get("lines", []):
                    line_text = ""
                    line_bbox = line.get("bbox")
                    for span in line.get("spans", []):
                        line_text += span.get("text", "")

                    if line_text.strip():
                        text_blocks.append({
                            "text": line_text.strip(),
                            "bbox": line_bbox,
                            "size": line.get("spans", [{}])[0].get("size", 12)
                        })
    except Exception as e:
        print(f"   ⚠️  Dict extraction failed: {e}")

    # Method 2: If dict fails, try blocks format
    if not text_blocks:
        try:
            blocks = page.get_text("blocks")
            for block in blocks:
                if len(block) >= 5:
                    text = block[4].strip()
                    bbox = (block[0], block[1], block[2], block[3])
                    if text:
                        text_blocks.append({
                            "text": text,
                            "bbox": bbox,
                            "size": 12
                        })
        except Exception as e:
            print(f"   ⚠️  Blocks extraction failed: {e}")

    # Method 3: Last resort - simple text extraction
    if not text_blocks:
        try:
            simple_text = page.get_text("text")
            if simple_text.strip():
                lines = [l.strip() for l in simple_text.split('\n') if l.strip()]
                rect = page.rect
                height_per_line = rect.height / max(len(lines), 1)

                for idx, line in enumerate(lines):
                    y0 = rect.y0 + (idx * height_per_line)
                    y1 = y0 + height_per_line
                    text_blocks.append({
                        "text": line,
                        "bbox": (rect.x0, y0, rect.x1, y1),
                        "size": 12
                    })
        except Exception as e:
            print(f"   ⚠️  Simple text extraction failed: {e}")

    return text_blocks


@app.route("/")
def index():
    """Health check endpoint."""
    return jsonify({
        "status": "running",
        "service": "PDF Translator",
        "translation_api": "MyMemory (free)",
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
        total_blocks = 0

        print(f"📖 Processing {total_pages} pages...")

        for page_num in range(total_pages):
            src_page = src_pdf[page_num]
            rect = src_page.rect

            out_page = out_pdf.new_page(width=rect.width, height=rect.height)
            out_page.show_pdf_page(rect, src_pdf, page_num)

            text_blocks = extract_text_blocks(src_page)

            if not text_blocks:
                print(f"   Page {page_num + 1}: ⚠️  No extractable text found")
                continue

            total_blocks += len(text_blocks)
            print(f"   Page {page_num + 1}: Found {len(text_blocks)} text blocks")

            # Translate all text
            texts = [block["text"] for block in text_blocks]
            translated_texts = translate_texts_batch(
                texts,
                target_lang=target_lang,
                source_lang=source_lang
            )

            # Cover original text and insert translations
            fontfile = FONT_FILE if os.path.exists(FONT_FILE) else None

            for idx, block in enumerate(text_blocks):
                bbox = block["bbox"]
                size = block.get("size", 12)

                rect_obj = fitz.Rect(bbox)

                # Cover original
                out_page.draw_rect(rect_obj, color=None, fill=(1, 1, 1), overlay=True)

                # Insert translated
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
        print(f"   Total text blocks translated: {total_blocks}")
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