import email
import os
from email.header import decode_header
from email.utils import parsedate_to_datetime

# ベースディレクトリ
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 添付ファイル保存先
ATTACH_DIR = os.path.join(BASE_DIR, "attachments")
os.makedirs(ATTACH_DIR, exist_ok=True)


def decode_mime_text(text):
    """
    件名・送信者用のデコード処理
    """
    if not text:
        return ""

    decoded_parts = decode_header(text)
    decoded_string = ""

    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            if encoding:
                decoded_string += part.decode(encoding, errors="ignore")
            else:
                decoded_string += part.decode(errors="ignore")
        else:
            decoded_string += part

    return decoded_string


def decode_filename(filename):
    """
    添付ファイル名のデコード
    """
    decoded_parts = decode_header(filename)
    decoded_string = ""

    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            if encoding:
                decoded_string += part.decode(encoding, errors="ignore")
            else:
                decoded_string += part.decode(errors="ignore")
        else:
            decoded_string += part

    # Windowsで使えない文字を除去
    decoded_string = decoded_string.replace("/", "_").replace("\\", "_").replace(":", "_").replace("*", "_").replace("?", "_").replace("\"", "_").replace("<", "_").replace(">", "_").replace("|", "_")

    return decoded_string


def parse_eml(file_path):
    """
    emlファイルを解析
    """

    with open(file_path, "rb") as f:
        msg = email.message_from_binary_file(f)

    # 件名・送信者デコード
    subject = decode_mime_text(msg.get("Subject"))
    sender = decode_mime_text(msg.get("From"))
    date_raw = msg.get("Date", "")

    try:
        date = parsedate_to_datetime(date_raw).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except:
        date = date_raw

    body = ""
    attachments = []

    for part in msg.walk():
        content_type = part.get_content_type()

        # ===== 本文処理 =====
        if content_type == "text/plain":
            payload = part.get_payload(decode=True)

            if payload:
                charset = part.get_content_charset()

                if charset:
                    try:
                        body += payload.decode(charset, errors="ignore")
                    except:
                        body += payload.decode("utf-8", errors="ignore")
                else:
                    # よくある日本語メール対応
                    try:
                        body += payload.decode("utf-8", errors="ignore")
                    except:
                        body += payload.decode("iso-2022-jp", errors="ignore")

        # ===== 添付処理 =====
        if part.get_filename():
            raw_filename = part.get_filename()

            # ファイル名デコード
            filename = decode_filename(raw_filename)

            filepath = os.path.join(ATTACH_DIR, filename)

            with open(filepath, "wb") as f:
                f.write(part.get_payload(decode=True))

            attachments.append(filepath)

    return {
        "subject": subject,
        "from": sender,
        "date": date,
        "body": body,
        "attachments": attachments
    }