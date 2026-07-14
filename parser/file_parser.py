import os
import pandas as pd
import pdfplumber
from docx import Document

# ==============================
# メイン
# ==============================
def parse_file(file_path):

    ext = os.path.splitext(file_path)[1].lower()

    if ext in [".xlsx", ".xls"]:
        return parse_excel(file_path)

    elif ext == ".pdf":
        return parse_pdf(file_path)

    elif ext in [".docx", ".doc"]:
        return parse_word(file_path)

    elif ext == ".csv":
        return parse_csv(file_path)

    else:
        return {"type": "unknown", "data": None}


# ==============================
# Excel
# ==============================
def parse_excel(file_path):

    df = pd.read_excel(file_path, engine="openpyxl")

    text = ""

    for col in df.columns:
        values = df[col].dropna().tolist()

        if values:
            text += f"{col}: " + ", ".join(map(str, values)) + "\n"

    return {
        "type": "text",
        "data": text
    }


# ==============================
# ✅ PDF
# ==============================
def parse_pdf(file_path):

    text = ""

    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""

    return {
        "type": "text",
        "data": text
    }


# ==============================
# ✅ Word
# ==============================
def parse_word(file_path):

    doc = Document(file_path)
    text = "\n".join([p.text for p in doc.paragraphs])

    return {
        "type": "text",
        "data": text
    }


# ==============================
# CSV
# ==============================

def parse_csv(file_path):

    encodings = [
        "utf-8",
        "cp932",
        "shift_jis"
    ]

    for enc in encodings:
        try:
            df = pd.read_csv(
                file_path,
                encoding=enc
            )

            return {
                "type": "text",
                "data": df.to_string(index=False)
            }

        except Exception:
            pass

    return {
        "type": "text",
        "data": ""
    }



# ==============================
# Excel構造パーサー
# ==============================
def parse_excel_structured(file_path):

    sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="openpyxl")

    result = {
        "name": None,
        "age": None,
        "nearest": None,
        "skills": [],
        "role": []
    }

    for sheet_name, df in sheets.items():
        df = df.fillna("")

        for i in range(len(df)):
            row = df.iloc[i].astype(str).tolist()
            row_text = " ".join(row)

            # =====================
            # 名前
            # =====================
            if "氏名" in row_text:
                for cell in row:
                    if " " in cell:
                        result["name"] = cell.strip()

            # =====================
            # 生年月日 → 年齢
            # =====================
            if "生年月日" in row_text:
                for cell in row:
                    if "19" in cell or "20" in cell:
                        result["age"] = None  # 必要なら計算

            # =====================
            # 最寄駅
            # =====================
            if "最寄駅" in row_text:
                for cell in row:
                    if "駅" in cell:
                        result["nearest"] = cell.strip()

            # =====================
            # スキル（SAP/ABAPなど）
            # =====================
            if "ABAP" in row_text:
                result["skills"].append("ABAP")

            if "SAP" in row_text:
                result["skills"].append("SAP")

            if "Java" in row_text:
                result["skills"].append("Java")

            # =====================
            # 役割
            # =====================
            if "リーダ" in row_text or "リーダー" in row_text:
                result["role"].append("リーダー")

            if "メンバー" in row_text:
                result["role"].append("メンバー")

    result["skills"] = list(set(result["skills"]))
    result["role"] = list(set(result["role"]))

    return result