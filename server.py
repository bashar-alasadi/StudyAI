from flask import Flask, request, jsonify, send_from_directory
from google import genai
from dotenv import load_dotenv
import tempfile
import os


# تحميل المفتاح من ملف .env
load_dotenv(override=True)

app = Flask(__name__)


# إنشاء عميل Gemini
client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)


@app.route("/")
def home():
    return "StudyAI server is running"


@app.route("/dashboard")
def dashboard():
    return send_from_directory(".", "dashboard.html")


@app.route("/style.css")
def style():
    return send_from_directory(".", "style.css")


@app.route("/upload", methods=["POST"])
def upload():

    if "audio" not in request.files:
        return jsonify({
            "error": "لم يتم إرسال ملف صوتي"
        }), 400

    file = request.files["audio"]
    temp_path = None

    try:
        extension = os.path.splitext(file.filename)[1].lower()

        allowed_extensions = [
            ".mp3",
            ".mp4",
            ".mpeg",
            ".mpga",
            ".m4a",
            ".wav",
            ".webm",
            ".ogg",
            ".flac"
        ]

        if extension not in allowed_extensions:
            return jsonify({
                "error": "صيغة الملف الصوتي غير مدعومة"
            }), 400

        # حفظ المحاضرة مؤقتًا
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=extension
        ) as temp:

            file.save(temp.name)
            temp_path = temp.name

        # رفع الملف الصوتي إلى Gemini
        uploaded_file = client.files.upload(
            file=temp_path
        )

        # طلب تفريغ المحاضرة كاملة إلى نص
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=[
                uploaded_file,
                """
قم بتفريغ هذه المحاضرة الصوتية إلى نص مكتوب بدقة عالية.

التعليمات:
- اكتب كل الكلام الموجود في المحاضرة.
- لا تلخص المحاضرة.
- لا تحذف أي معلومات مهمة.
- حافظ على ترتيب الكلام كما ورد في التسجيل.
- إذا كان التسجيل باللغة العربية فاكتب النص باللغة العربية.
- ضع علامات الترقيم المناسبة.
- قسّم النص إلى فقرات ليسهل قراءته.
- إذا لم تكن كلمة معينة واضحة، لا تخترع كلمة من عندك.
- المطلوب هو تفريغ صوتي فقط وليس تلخيصًا.
"""
            ]
        )

        transcription_text = response.text

        if not transcription_text:
            return jsonify({
                "error": "لم يتم استخراج نص من الملف الصوتي"
            }), 500

        return jsonify({
            "message": "تم تحويل المحاضرة إلى نص بنجاح",
            "filename": file.filename,
            "text": transcription_text
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500

    finally:
        # حذف الملف المؤقت من الكمبيوتر
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


if __name__ == "__main__":
    app.run(
        debug=True,
        use_reloader=False
    )