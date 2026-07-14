from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi import Form
import csv
import shutil
import os
import json
import re
from db.database import find_duplicate_candidate, find_duplicate_candidates
from db.database import get_candidate_by_id
from db.database import update_review_status, delete_candidate
from db.database import update_candidate
from typing import List

from parser.mail_parser import parse_eml
from parser.extractor import extract_all
from parser.file_parser import parse_file
from db.database import init_db, insert_candidate, get_all_candidates
from ai_helper import call_ai_extract
from datetime import datetime


app = FastAPI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

init_db()

# ==============================
# 正規化
# ==============================
def normalize_station(name):
    if not name:
        return name
    import re
    name = re.sub(r'.*線', '', name)
    name = name.replace("　", "").replace(" ", "")
    if "駅" not in name:
        name += "駅"
    return name


def normalize_name(name):
    if not name:
        return name
    import re
    name = re.sub(r'[（(].*?[）)]', '', name)
    return name.strip()


def normalize_skill(s):
    s_low = s.lower()
    if "oracle" in s_low:
        return "Oracle"
    if "windows" in s_low:
        return "Windows"
    if "sql" in s_low:
        return "SQL"
    if "aws" in s_low:
        return "AWS"
    if "javascript" in s_low:
        return "JavaScript"
    return s.strip()


def normalize_role(r):
    if not r:
        return None

    r = r.lower()

    if any(x in r for x in ["規模", "業種", "人数"]):
        return None

    if "pm" in r:
        return "PM"
    if "pl" in r:
        return "PL"
    if "se" in r:
        return "SE"
    if "プログラマー" in r or "pg" in r or "メンバ" in r:
        return "PG"

    return None


def preprocess_text(text):
    import re

    text = re.sub(r'(\S+線)\s+(\S+駅)', r'\2', text)
    text = re.sub(r'(\S+)\s+駅', r'\1駅', text)

    return text


# ==============================
#  経験年数集計
# ==============================
def calc_total_experience(projects):

    total_months = 0

    for p in projects:
        total_months += p.get("duration_months", 0)

    return {
        "months": total_months,
        "years": round(total_months / 12)
    }

# ==============================
# 確認待ち判定
# ==============================
def check_review_needed(data, check_duplicate=True):

    reasons = []

    if not (data.get("name") or "").strip():
        reasons.append("氏名未取得")

    if not data.get("price"):
        reasons.append("単価未取得")

    # ==================
    # スキルチェック
    # ==================
    skills = data.get("skills")

    if isinstance(skills, list):

        if len(skills) == 0:
            reasons.append("スキル未取得")

    else:

        if not (skills or "").strip():
            reasons.append("スキル未取得")

    # ==================

    if not (data.get("nearest") or "").strip():
        reasons.append("最寄駅未取得")

    # ==================
    # 重複候補判定
    # ==================
    if check_duplicate:

        if data.get("name") and data.get("nearest"):

            duplicate = find_duplicate_candidate(
                data.get("name"),
                data.get("nearest")
            )

            if duplicate:
                reasons.append("重複候補の可能性")

    # ==================

    if reasons:
        return (
            "pending",
            "、".join(reasons)
        )

    return (
        "approved",
        ""
    )

# ==============================
# TOP
# ==============================
@app.get("/")

def index():

    return RedirectResponse(
        url="/search",
        status_code=302
    )

# def root():
#     return {"message": "Skill System Running"}


# ==============================
# メール取り込み
# ==============================
@app.post("/upload_eml/")
async def upload_eml(
    files: List[UploadFile] = File(...)
):

    results = []

    if len(files) > 20:
        return {
            "error": "一度にアップロードできるのは20件までです"
        }

    for file in files:

        file_path = os.path.join(UPLOAD_DIR, file.filename)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        data = parse_eml(file_path)

        body_text = data["body"]
        attachment_text = ""

        attachment_files = []
        for att in data["attachments"]:
            result = parse_file(att)
            attachment_files.append(att)
            if result["type"] == "text":
                attachment_text += result["data"]
        

        # ==============================
        # 正規抽出
        # ==============================
        body_ex = extract_all(body_text)
        file_ex = extract_all(attachment_text)

        def pick(body_val, file_val):
            return body_val if body_val else file_val

        db_data = {
            "name": pick(body_ex["basic"].get("name"), file_ex["basic"].get("name")),
            "age": pick(body_ex["basic"].get("age"), file_ex["basic"].get("age")),
            "price": pick(body_ex["work_condition"].get("price"), file_ex["work_condition"].get("price")),
            "nearest": pick(body_ex["basic"].get("nearest"), file_ex["basic"].get("nearest")),
            "company": pick(body_ex["basic"].get("company"), file_ex["basic"].get("company")),
            "start": pick(body_ex["work_condition"].get("start"), file_ex["work_condition"].get("start")),
            "remote": pick(body_ex["work_condition"].get("remote"), file_ex["work_condition"].get("remote")),
            "skills": sum(body_ex["skills"].values(), []) + sum(file_ex["skills"].values(), [])
        }
        db_data["mail_path"] = file_path
        db_data["attachment_paths"] = "|".join(
            attachment_files
        )

        # ==============================
        # AI
        # ==============================
        raw_text = (
            "【メール本文】\n" + body_text +
            "\n\n【添付ファイル】\n" + attachment_text
        )

        clean_text = preprocess_text(raw_text)
        ai_data = call_ai_extract(clean_text)


        # ==============================
        # 経験年数
        # ==============================
        projects = ai_data.get("projects", [])

        total_exp = calc_total_experience(projects)

        db_data["total_experience_months"] = total_exp["months"]
        db_data["total_experience_years"] = total_exp["years"]

        # ==============================
        # skills（一覧）
        # ==============================
        if ai_data.get("skills"):
            raw_skills = [s["name"] for s in ai_data["skills"]]
        else:
            raw_skills = db_data["skills"]

        exclude_keywords = [
            "PMO",
            "運用保守",
            "リーダー",
            "上流",
            "顧客折衝"
        ]

        raw_skills = [
            s for s in raw_skills
            if not any(
                keyword in s
                for keyword in exclude_keywords
            )
        ]

        db_data["skills"] = list({
            normalize_skill(s)
            for s in raw_skills
        })

        # ==============================
        # role
        # ==============================
        if ai_data.get("role"):
            db_data["role"] = ai_data["role"]
        else:
            db_data["role"] = (
                body_ex["experience"].get("role", [])
                + file_ex["experience"].get("role", [])
            )

        # ==============================
        # 基本情報（AI優先）
        # ==============================
        if ai_data.get("name"):
            db_data["name"] = normalize_name(ai_data["name"])

        if ai_data.get("age"):
            db_data["age"] = int(ai_data["age"])

        if ai_data.get("nearest"):
            db_data["nearest"] = normalize_station(ai_data["nearest"])

        if ai_data.get("price"):
            m = re.search(r"\d+", str(ai_data["price"]))
            if m:
                db_data["price"] = int(m.group())

        start = ai_data.get("start", "").strip()

        if any(keyword in start for keyword in [
            "即日",
            "ASAP",
            "随時",
            "即稼働",
            "即参画",
            "即可能"
        ]):
            start = datetime.now().strftime("%Y/%m/01")

        if start:
            db_data["start"] = start
        
        if ai_data.get("remote"):
            db_data["remote"] = ai_data["remote"]
        
        if ai_data.get("vendor_type"):
            db_data["vendor_type"] = ai_data["vendor_type"]
        else:
            db_data["vendor_type"] = ""

        # ==============================
        # 経験
        # ==============================
        exp = ai_data.get("experience", {})
        db_data["exp_dev"] = exp.get("exp_dev", False)
        db_data["exp_infra"] = exp.get("exp_infra", False)
        db_data["exp_ops"] = exp.get("exp_ops", False)
        db_data["exp_pmo"] = exp.get("exp_pmo", False)
        db_data["exp_requirements"] = exp.get("exp_requirements", False)
        db_data["exp_design"] = exp.get("exp_design", False)
        db_data["exp_test"] = exp.get("exp_test", False)
        db_data["exp_operation"] = exp.get("exp_operation", False)

        cond = ai_data.get("conditions", {})
        db_data["has_upstream"] = cond.get("has_upstream", False)
        db_data["has_leader"] = cond.get("has_leader", False)
        db_data["has_client_contact"] = cond.get("has_client_contact", False)

        # ==============================
        # 最終整形
        # ==============================
        db_data["name"] = normalize_name(db_data["name"])
        db_data["nearest"] = normalize_station(db_data["nearest"])

        db_data["role"] = list(set([
            normalize_role(r)
            for r in db_data.get("role", [])
            if normalize_role(r)
        ]))

        # ==============================
        # 登録
        # ==============================
        review_status, review_reason = check_review_needed(
            db_data
        )

        db_data["review_status"] = review_status
        db_data["review_reason"] = review_reason

        insert_candidate(db_data)

        results.append({
            "file": file.filename,
            "name": db_data.get("name"),
            "review_status": review_status,
            "review_reason": review_reason
        })

    success_count = 0
    pending_count = 0

    for r in results:

        if r["review_status"] == "pending":
            pending_count += 1
        else:
            success_count += 1
    
    html = f"""
    <html>
    <body>

    <h1>取込完了</h1>

    <h2>結果サマリー</h2>

    成功：{success_count}件<br>
    確認待ち：{pending_count}件<br><br>

    <hr>
    """

    for r in results:

        status_text = (
            "⚠ 要確認"
            if r["review_status"] == "pending"
            else "✅ 成功"
        )

        status_color = (
            "red"
            if r["review_status"] == "pending"
            else "green"
        )

        html += f"""
        <b>ファイル</b>：{r['file']}<br>
        <b>氏名</b>：{r['name'] or '未取得'}<br>
        <b>状態</b>：
        <span style="color:{status_color}; font-weight:bold;">
        {status_text}
        </span>
        <br>

        <b>理由</b>：{r['review_reason'] or ''}<br>

        <hr>
        """
    
    html += """
    <br>

    <a href="/search">
    <button>
    検索画面へ戻る
    </button>
    </a>

    </body>
    </html>
    """
    return HTMLResponse(content=html)

# ==============================
#  一覧
# ==============================

@app.get("/candidates", response_class=HTMLResponse)
def show_candidates():

    rows = get_all_candidates()

    html = "<h1>候補者一覧</h1>"
    html += "<a href='/search'>検索へ</a><br><br>"

    html += "<table border='1' cellpadding='5'>"
    html += """
    <tr>
    <th>ID</th>
    <th>名前</th>
    <th>年齢</th>
    <th>単価</th>
    <th>最寄</th>
    <th>会社</th>
    <th>稼働</th>
    <th>リモート</th>
    <th>経験年数</th>
    <th>スキル</th>
    <th>役割</th>
    <th>上流</th>
    <th>リーダー</th>
    <th>顧客折衝</th>
    <th>開発</th>
    <th>インフラ</th>
    <th>運用保守</th>
    <th>PMO</th>
    <th>要件定義</th>
    <th>設計</th>
    <th>テスト</th>
    <th>更新日</th>
    <th>メール</th>
    <th>添付</th>
    </tr>
    """

    for r in rows:

        (
            db_id,
            db_name,
            db_age,
            db_price,
            db_nearest,
            db_company,
            db_start,
            db_remote,
            db_total_exp,
            db_skills,
            db_role,

            db_exp_dev,
            db_exp_infra,
            db_exp_ops,
            db_exp_pmo,

            db_exp_requirements,
            db_exp_design,
            db_exp_test,

            db_has_upstream,
            db_has_leader,
            db_has_client_contact,

            db_source,
            db_created,
            db_updated,
            db_attachment_paths

        ) = r

        if db_attachment_paths:
            attachment_link = f'<a href="/attachment/{db_id}">確認</a>'
        else:
            attachment_link = '-'

        html += f"""
        <tr>
            <td>{db_id}</td>
            <td>{db_name}</td>
            <td>{db_age}</td>
            <td>{db_price}</td>
            <td>{db_nearest}</td>
            <td>{db_company}</td>
            <td>{db_start}</td>
            <td>{db_remote}</td>
            <td>{db_total_exp}</td>
            <td>{db_skills}</td>
            <td>{db_role}</td>
            <td>{'〇' if db_has_upstream else ''}</td>
            <td>{'〇' if db_has_leader else ''}</td>
            <td>{'〇' if db_has_client_contact else ''}</td>
            <td>{'〇' if db_exp_dev else ''}</td>
            <td>{'〇' if db_exp_infra else ''}</td>
            <td>{'〇' if db_exp_ops else ''}</td>
            <td>{'〇' if db_exp_pmo else ''}</td>
            <td>{'〇' if db_exp_requirements else ''}</td>
            <td>{'〇' if db_exp_design else ''}</td>
            <td>{'〇' if db_exp_test else ''}</td>
            <td>{db_updated[:10]}</td>
            <td>
            <a href="/mail/{db_id}">
            確認
            </a>
            </td>

            <td>{attachment_link}</td>

        </tr>
        """

    html += "</table>"

    return html


# ==============================
# 検索
# ==============================
@app.get("/search", response_class=HTMLResponse)
def search(
    skill: str = "",
    min_price: str = "",
    name: str = "",
    min_exp: str = "",
    company: str = "",
    start_from: str = "",
    nearest: str = "",
    remote_ok: str = "",
    role: str = "",

    upstream: str = "",
    leader: str = "",
    client_contact: str = "",

    dev: str = "",
    infra: str = "",
    ops: str = "",
    pmo: str = "",
    requirements: str = "",
    design: str = "",
    test: str = "",
    updated_from: str = "",
    review_only: str = "",
    vendor_type: str = ""
):

    # ===== 日付変換 =====
    start_from_db = ""

    if start_from:
        start_from_db = start_from.replace("-", "/")

    # ===== 単価変換 =====
    if min_price == "":
        min_price_value = None
    else:
        try:
            min_price_value = int(min_price)
        except:
            min_price_value = None

    rows = get_all_candidates()

    # ===== フィルタ =====
    filtered = []

    for row in rows:
        (
            db_id,
            db_name,
            db_age,
            db_price,
            db_nearest,
            db_company,
            db_start,
            db_remote,
            db_total_exp,
            db_skills,
            db_role,

            db_exp_dev,
            db_exp_infra,
            db_exp_ops,
            db_exp_pmo,

            db_exp_requirements,
            db_exp_design,
            db_exp_test,

            db_has_upstream,
            db_has_leader,
            db_has_client_contact,

            db_source,
            db_created,
            db_updated,
            db_attachment_paths,
            db_review_status,
            db_review_reason,
            db_vendor_type
        ) = row

        # スキル
        if skill:

            skill_list = [
                s.strip().lower()
                for s in skill.split(",")
                if s.strip()
            ]

            skill_text = (db_skills or "").lower()

            if not all(s in skill_text for s in skill_list):
                continue
        
        # 役割
        if role:

            role_list = [
                r.strip().upper()
                for r in role.split(",")
                if r.strip()
            ]

            role_text = (db_role or "").upper()

            if not any(r in role_text for r in role_list):
                continue

        # 単価
        if min_price_value and (db_price is None or db_price < min_price_value):
            continue

        # 名前
        if name and name not in (db_name or ""):
            continue
        
        # 経験年数
        if min_exp:
            try:
                if (db_total_exp or 0) < int(min_exp):
                    continue
            except:
                pass

        # 会社
        if company and company.lower() not in (db_company or "").lower():
            continue

        # 一社先
        if vendor_type:

            if db_vendor_type != vendor_type:
                continue
        
        # 稼働開始日
        if start_from_db:

            if not db_start:
                continue

            if db_start[:10] > start_from_db:
                continue

        # 最寄
        if nearest and nearest.lower() not in (db_nearest or "").lower():
            continue

        # リモート
        if remote_ok and not db_remote:
            continue

        # 上流
        if upstream and not db_has_upstream:
            continue

        # リーダー
        if leader and not db_has_leader:
            continue

        # 顧客折衝
        if client_contact and not db_has_client_contact:
            continue
        
        if dev and not db_exp_dev:
            continue

        if infra and not db_exp_infra:
            continue

        if ops and not db_exp_ops:
            continue

        if pmo and not db_exp_pmo:
            continue

        if requirements and not db_exp_requirements:
            continue

        if design and not db_exp_design:
            continue

        if test and not db_exp_test:
            continue

        if updated_from:
            if not db_updated:
                continue

            if db_updated[:10] < updated_from:
                continue
        
        # 確認待ちのみ
        if review_only:

            if db_review_status != "pending":
                continue

        filtered.append(row)

    # ===== HTML =====
    html = f"""
    <html>
    <head>
    <style>

    body{{
        font-family:"Segoe UI",sans-serif;
        background:#f5f7fa;
        margin:20px;
    }}

    h1{{
        color:#1f4e79;
        border-bottom:3px solid #1f4e79;
        padding-bottom:10px;
        margin-bottom:20px;
    }}

    h2{{
        color:#2f75b5;
        margin-top:0;
    }}

    .card{{
        background:white;
        padding:20px;
        margin-bottom:20px;
        border-radius:10px;
        box-shadow:0 2px 8px rgba(0,0,0,0.1);
    }}

    table{{
        width:100%;
        border-collapse:collapse;
        background:white;
    }}

    th{{
        background:#1f4e79;
        color:white;
        padding:10px;
        position:sticky;
        top:0;
        z-index:100;
    }}

    td{{
        padding:8px;
    }}

    table td{{
        max-width:200px;
        word-break:break-word;
    }}

    th,td{{
        border:1px solid #d0d7de;
    }}

    tr:nth-child(even){{
        background:#f8fbff;
    }}

    input[type=text],
    input[type=date]{{
        padding:6px;
        border:1px solid #ccc;
        border-radius:5px;
    }}

    input[type=submit],
    button{{
        background:#2f75b5;
        color:white;
        border:none;
        padding:8px 15px;
        border-radius:5px;
        cursor:pointer;
    }}

    input[type=submit]:hover,
    button:hover{{
        background:#1f4e79;
    }}

    .ok{{
        color:#28a745;
        font-weight:bold;
    }}

    .pending{{
        color:#dc3545;
        font-weight:bold;
    }}

    .action-btn{{
        display:inline-block;
        background:#2f75b5;
        color:white;
        padding:5px 10px;
        border-radius:5px;
        text-decoration:none;
    }}

    .action-btn:hover{{
        background:#1f4e79;
    }}

    .table-container{{
        max-height:700px;
        overflow:auto;
    }}
    </style>
    </head>
    <body>

    <h1>人材管理システム</h1>

    <div class="card">

    <h2>メール取込</h2>

    <form
        action="/upload_eml/"
        method="post"
        enctype="multipart/form-data">

        <input
            type="file"
            name="files"
            multiple
            accept=".eml">

        <input
            type="submit"
            value="取込"
            onclick="this.value='取り込み中...'; this.disabled=true; this.form.submit();">

    </form>

    <p style="color:#666; font-size:12px;">
    ※ Ctrlキーを押しながら複数のメール（.eml）を選択できます。<br>
    ※ 一度に最大20件まで取り込み可能です。<br>
    ※ 添付ファイルの内容や件数によっては、取り込み完了まで時間がかかる場合があります。
    </p>

    </div>

    <div class="card">

    <h2>検索</h2>

    <div class="card">

    <form method="get">

    <fieldset>
    <legend><b>基本条件</b></legend>

    名前：
    <input
        type="text"
        name="name"
        value="{name}">

    会社：
    <input
        type="text"
        name="company"
        value="{company}">
    
    商流：

    <select name="vendor_type">

    <option value="">
    全て
    </option>

    <option
        value="一社先"
        {"selected" if vendor_type == "一社先" else ""}
    >
    一社先
    </option>

    <option
        value="直"
        {"selected" if vendor_type == "直" else ""}
    >
    直
    </option>

    </select>

    最寄駅：
    <input
        type="text"
        name="nearest"
        value="{nearest}">

    <br><br>

    単価以上：
    <input
        type="text"
        name="min_price"
        value="{min_price}">

    経験年数以上：
    <input
        type="text"
        name="min_exp"
        value="{min_exp}">

    稼働開始日以前：
    <input
        type="date"
        name="start_from"
        value="{start_from}">

    </fieldset>

    <br>

    <fieldset>
    <legend><b>スキル・役割</b></legend>

    スキル：
    <input
        type="text"
        name="skill"
        value="{skill}"
        placeholder="例: Java,AWS,Oracle">

    <br><br>

    役割：
    <input
        type="text"
        name="role"
        value="{role}"
        placeholder="例: PM,PL,SE">

    </fieldset>

    <br>

    <fieldset>
    <legend><b>経験区分</b></legend>

    <label>
    <input
        type="checkbox"
        name="dev"
        value="1"
        {"checked" if dev else ""}>
    開発
    </label>

    <label>
    <input
        type="checkbox"
        name="infra"
        value="1"
        {"checked" if infra else ""}>
    インフラ
    </label>

    <label>
    <input
        type="checkbox"
        name="ops"
        value="1"
        {"checked" if ops else ""}>
    運用保守
    </label>

    <label>
    <input
        type="checkbox"
        name="pmo"
        value="1"
        {"checked" if pmo else ""}>
    PMO
    </label>

    <br><br>

    <label>
    <input
        type="checkbox"
        name="requirements"
        value="1"
        {"checked" if requirements else ""}>
    要件定義
    </label>

    <label>
    <input
        type="checkbox"
        name="design"
        value="1"
        {"checked" if design else ""}>
    設計
    </label>

    <label>
    <input
        type="checkbox"
        name="test"
        value="1"
        {"checked" if test else ""}>
    テスト
    </label>

    </fieldset>

    <br>

    <fieldset>
    <legend><b>実績・特性</b></legend>

    <label>
    <input
        type="checkbox"
        name="upstream"
        value="1"
        {"checked" if upstream else ""}>
    上流経験
    </label>

    <label>
    <input
        type="checkbox"
        name="leader"
        value="1"
        {"checked" if leader else ""}>
    リーダー経験
    </label>

    <label>
    <input
        type="checkbox"
        name="client_contact"
        value="1"
        {"checked" if client_contact else ""}>
    顧客折衝経験
    </label>

    <label>
    <input
        type="checkbox"
        name="remote_ok"
        value="1"
        {"checked" if remote_ok else ""}>
    リモート希望あり
    </label>

    </fieldset>

    <br>

    <fieldset>
    <legend><b>管理</b></legend>

    更新日以降：

    <input
        type="date"
        name="updated_from"
        value="{updated_from}">

    <label>
    <input
        type="checkbox"
        name="review_only"
        value="1"
        {"checked" if review_only else ""}>
    確認待ちのみ
    </label>

    </fieldset>

    <br><br>

    <input
        type="submit"
        value="検索">

    <button
        type="button"
        onclick="window.location='/search'">
    リセット
    </button>

    </form>

    <br>

    <a href="/export_csv?skill={skill}&role={role}&min_price={min_price}&name={name}&company={company}&vendor_type={vendor_type}&nearest={nearest}&min_exp={min_exp}&start_from={start_from}&remote_ok={remote_ok}&upstream={upstream}&leader={leader}&client_contact={client_contact}&dev={dev}&infra={infra}&ops={ops}&pmo={pmo}&requirements={requirements}&design={design}&test={test}&updated_from={updated_from}&review_only={review_only}">

    <button type="button">
    CSV出力
    </button>

    </a>

    </div>
    <div class="card">

    <h3>{len(filtered)}件ヒット</h3>

    <div class="table-container">
    <table border="1" cellpadding="5">
        <tr>
            <th>ID</th>
            <th>名前</th>
            <th>年齢</th>
            <th>単価</th>
            <th>最寄</th>
            <th>会社</th>
            <th>商流</th>
            <th>稼働</th>
            <th>リモート</th>
            <th>経験年数</th>
            <th>スキル</th>
            <th>役割</th>
            <th>上流</th>
            <th>リーダー</th>
            <th>顧客折衝</th>
            <th>開発</th>
            <th>インフラ</th>
            <th>運用保守</th>
            <th>PMO</th>
            <th>要件定義</th>
            <th>設計</th>
            <th>テスト</th>
            <th>更新日</th>
            <th>メール</th>
            <th>添付</th>
            <th>編集</th>
            <th>確認状態</th>
            <th>確認理由</th>
        </tr>
    """

    for row in filtered:

        (
            db_id,
            db_name,
            db_age,
            db_price,
            db_nearest,
            db_company,
            db_start,
            db_remote,
            db_total_exp,
            db_skills,
            db_role,

            db_exp_dev,
            db_exp_infra,
            db_exp_ops,
            db_exp_pmo,

            db_exp_requirements,
            db_exp_design,
            db_exp_test,

            db_has_upstream,
            db_has_leader,
            db_has_client_contact,

            db_source,
            db_created,
            db_updated,
            db_attachment_paths,
            db_review_status,
            db_review_reason,
            db_vendor_type
        ) = row

        mail_link = f'<a href="/mail/{db_id}">確認</a>'

        if db_attachment_paths:
            attachment_link = f'<a href="/attachment/{db_id}">確認</a>'
        else:
            attachment_link = "-"
        
        if db_review_status == "pending":

            if "重複候補" in (db_review_reason or ""):
                review_link = f'<a href="/review/{db_id}">確認待ち</a>'
            else:
                review_link = f'<a href="/edit/{db_id}">確認待ち</a>'
        else:
            review_link = "OK"
        
        skill_text = str(db_skills).replace(",", "<br>")

        html += f"""
        <tr>
            <td>{db_id}</td>
            <td>{db_name}</td>
            <td>{db_age}</td>
            <td>{db_price}</td>
            <td>{db_nearest}</td>
            <td>{db_company}</td>
            <td>{db_vendor_type}</td>
            <td>{db_start}</td>
            <td>{db_remote}</td>
            <td>{db_total_exp}</td>
            <td>{skill_text}</td>
            <td>{db_role}</td>
            <td>{'〇' if db_has_upstream else ''}</td>
            <td>{'〇' if db_has_leader else ''}</td>
            <td>{'〇' if db_has_client_contact else ''}</td>
            <td>{'〇' if db_exp_dev else ''}</td>
            <td>{'〇' if db_exp_infra else ''}</td>
            <td>{'〇' if db_exp_ops else ''}</td>
            <td>{'〇' if db_exp_pmo else ''}</td>
            <td>{'〇' if db_exp_requirements else ''}</td>
            <td>{'〇' if db_exp_design else ''}</td>
            <td>{'〇' if db_exp_test else ''}</td>
            <td>{db_updated[:10]}</td>
            <td>{mail_link}</td>
            <td>{attachment_link}</td>
            <td>
                <a href="/edit/{db_id}">
                    編集
                </a>
            </td>
            <td>{review_link}</td>
            <td>{db_review_reason or "-"}</td>
        </tr>
        """


    html += """
    </table>
    </div>
    </div>
    </body>
    </html>
    """

    return html

@app.get("/mail/{candidate_id}", response_class=HTMLResponse)
def show_mail(candidate_id: int):

    row = get_candidate_by_id(candidate_id)

    if not row:
        return "候補者が存在しません"

    mail_path = row[27]

    if not os.path.exists(mail_path):
        return "メールファイルが存在しません"

    mail_data = parse_eml(mail_path)

    subject = mail_data.get("subject", "")
    sender = mail_data.get("from", "")
    date = mail_data.get("date", "")
    body = mail_data.get("body", "")

    return f"""
    <html>
    <body>

    <h2>メール情報</h2>

    <table border="1" cellpadding="5">
        <tr>
            <th>件名</th>
            <td>{subject}</td>
        </tr>
        <tr>
            <th>送信元</th>
            <td>{sender}</td>
        </tr>
        <tr>
            <th>受信日時</th>
            <td>{date}</td>
        </tr>
    </table>

    <br>

    <h3>本文</h3>

    <pre>{body}</pre>

    <br>
    <button onclick="history.back()">戻る</button>

    </body>
    </html>
    """

@app.get("/attachment/{candidate_id}")
def download_attachment(candidate_id: int):

    row = get_candidate_by_id(candidate_id)

    if not row:
        return {"error": "候補者が存在しません"}

    attachment_path = row[28]

    if not attachment_path:
        return {"error": "添付ファイルがありません"}

    if not os.path.exists(attachment_path):
        return {"error": "ファイルが存在しません"}

    return FileResponse(
        path=attachment_path,
        filename=os.path.basename(attachment_path)
    )

@app.get("/export_csv")
def export_csv(
    skill: str = "",
    min_price: str = "",
    name: str = "",
    min_exp: str = "",
    company: str = "",
    start_from: str = "",
    nearest: str = "",
    remote_ok: str = "",
    role: str = "",

    upstream: str = "",
    leader: str = "",
    client_contact: str = "",

    dev: str = "",
    infra: str = "",
    ops: str = "",
    pmo: str = "",
    requirements: str = "",
    design: str = "",
    test: str = "",
    updated_from: str = "",
    review_only: str = "",
    vendor_type: str = ""
):

    # ===== 日付変換 =====
    start_from_db = ""

    if start_from:
        start_from_db = start_from.replace("-", "/")

    # ===== 単価変換 =====
    if min_price == "":
        min_price_value = None
    else:
        try:
            min_price_value = int(min_price)
        except:
            min_price_value = None
    
    rows = get_all_candidates()

    filtered = []

    for row in rows:

        (
            db_id,
            db_name,
            db_age,
            db_price,
            db_nearest,
            db_company,
            db_start,
            db_remote,
            db_total_exp,
            db_skills,
            db_role,

            db_exp_dev,
            db_exp_infra,
            db_exp_ops,
            db_exp_pmo,

            db_exp_requirements,
            db_exp_design,
            db_exp_test,

            db_has_upstream,
            db_has_leader,
            db_has_client_contact,

            db_source,
            db_created,
            db_updated,
            db_attachment_paths,
            db_review_status,
            db_review_reason,
            db_vendor_type
        ) = row


        # スキル
        if skill:

            skill_list = [
                s.strip().lower()
                for s in skill.split(",")
                if s.strip()
            ]

            skill_text = (db_skills or "").lower()

            if not all(s in skill_text for s in skill_list):
                continue
        
        # 役割
        if role:

            role_list = [
                r.strip().upper()
                for r in role.split(",")
                if r.strip()
            ]

            role_text = (db_role or "").upper()

            if not any(r in role_text for r in role_list):
                continue

        # 単価
        if min_price_value and (db_price is None or db_price < min_price_value):
            continue

        # 名前
        if name and name not in (db_name or ""):
            continue
        
        # 経験年数
        if min_exp:
            try:
                if (db_total_exp or 0) < int(min_exp):
                    continue
            except:
                pass

        # 会社
        if company and company.lower() not in (db_company or "").lower():
            continue

        # 一社先
        if vendor_type:

            if db_vendor_type != vendor_type:
                continue
        
        # 稼働開始日
        if start_from_db:

            if not db_start:
                continue

            if db_start[:10] > start_from_db:
                continue

        # 最寄
        if nearest and nearest.lower() not in (db_nearest or "").lower():
            continue

        # リモート
        if remote_ok and not db_remote:
            continue

        # 上流
        if upstream and not db_has_upstream:
            continue

        # リーダー
        if leader and not db_has_leader:
            continue

        # 顧客折衝
        if client_contact and not db_has_client_contact:
            continue
        
        if dev and not db_exp_dev:
            continue

        if infra and not db_exp_infra:
            continue

        if ops and not db_exp_ops:
            continue

        if pmo and not db_exp_pmo:
            continue

        if requirements and not db_exp_requirements:
            continue

        if design and not db_exp_design:
            continue

        if test and not db_exp_test:
            continue

        if updated_from:
            if not db_updated:
                continue

            if db_updated[:10] < updated_from:
                continue
        
        if review_only:

            if db_review_status != "pending":
                continue
    
        filtered.append(row)

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

    csv_file = f"candidates_export_{timestamp}.csv"


    with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:

        writer = csv.writer(f)

        writer.writerow([
            "ID",
            "氏名",
            "年齢",
            "単価",
            "最寄駅",
            "会社",
            "商流",
            "稼働開始日",
            "リモート",
            "経験年数",
            "スキル",
            "役割",
            "上流",
            "リーダー",
            "顧客折衝",
            "開発",
            "インフラ",
            "運用保守",
            "PMO",
            "要件定義",
            "設計",
            "テスト",
            "更新日"
        ])

        for row in filtered:

            (
            db_id,
            db_name,
            db_age,
            db_price,
            db_nearest,
            db_company,
            db_start,
            db_remote,
            db_total_exp,
            db_skills,
            db_role,

            db_exp_dev,
            db_exp_infra,
            db_exp_ops,
            db_exp_pmo,

            db_exp_requirements,
            db_exp_design,
            db_exp_test,

            db_has_upstream,
            db_has_leader,
            db_has_client_contact,

            db_source,
            db_created,
            db_updated,
            db_attachment_paths,
            db_review_status,
            db_review_reason,
            db_vendor_type
            ) = row

            writer.writerow([
                db_id,
                db_name,
                db_age,
                db_price,
                db_nearest,
                db_company,
                db_vendor_type,
                db_start,
                db_remote,
                db_total_exp,
                db_skills,
                db_role,
                "〇" if db_has_upstream else "",
                "〇" if db_has_leader else "",
                "〇" if db_has_client_contact else "",
                "〇" if db_exp_dev else "",
                "〇" if db_exp_infra else "",
                "〇" if db_exp_ops else "",
                "〇" if db_exp_pmo else "",
                "〇" if db_exp_requirements else "",
                "〇" if db_exp_design else "",
                "〇" if db_exp_test else "",
                db_updated[:10] if db_updated else ""
            ])

    return FileResponse(
        csv_file,
        filename=csv_file
    )

# review() 横並び比較版
@app.get("/review/{candidate_id}", response_class=HTMLResponse)
def review(candidate_id: int):

    target = get_candidate_by_id(candidate_id)

    if not target:
        return "候補者が存在しません"

    duplicates = find_duplicate_candidates(
        target[1],
        target[3]
    )

    candidate_dup = None

    for dup in duplicates:
        if dup[0] != candidate_id:
            candidate_dup = dup
            break

    if not candidate_dup:
        return "重複候補が見つかりません"

    mail_link_target = f"<a href='/mail/{target[0]}'>確認</a>"
    mail_link_dup = f"<a href='/mail/{candidate_dup[0]}'>確認</a>"

    attachment_link_target = (
        f"<a href='/attachment/{target[0]}'>確認</a>"
        if target[28] else "-"
    )

    attachment_link_dup = (
        f"<a href='/attachment/{candidate_dup[0]}'>確認</a>"
        if candidate_dup[28] else "-"
    )

    html = f"""
    <html>
    <body>

    <h2>重複候補比較</h2>

    <table border='1' cellpadding='5'>

        <tr>
            <th>項目</th>
            <th>対象候補</th>
            <th>重複候補</th>
        </tr>

        <tr><td>ID</td><td>{target[0]}</td><td>{candidate_dup[0]}</td></tr>
        <tr><td>名前</td><td>{target[1]}</td><td>{candidate_dup[1]}</td></tr>
        <tr><td>年齢</td><td>{target[2]}</td><td>{candidate_dup[2]}</td></tr>
        <tr><td>単価</td><td>{target[6]}</td><td>{candidate_dup[6]}</td></tr>
        <tr><td>最寄駅</td><td>{target[3]}</td><td>{candidate_dup[3]}</td></tr>
        <tr><td>会社</td><td>{target[4]}</td><td>{candidate_dup[4]}</td></tr>
        <tr><td>商流</td><td>{target[32]}</td><td>{candidate_dup[32]}</td></tr>
        <tr><td>稼働開始日</td><td>{target[7]}</td><td>{candidate_dup[7]}</td></tr>
        <tr><td>リモート</td><td>{target[8]}</td><td>{candidate_dup[8]}</td></tr>

        <tr><td>経験年数</td><td>{target[22]}</td><td>{candidate_dup[22]}</td></tr>
        <tr><td>スキル</td><td>{str(target[23] or '').replace(',', '<br>')}</td><td>{str(candidate_dup[23] or '').replace(',', '<br>')}</td></tr>
        <tr><td>役割</td><td>{target[24]}</td><td>{candidate_dup[24]}</td></tr>

        <tr><td>上流</td><td>{'〇' if target[18] else ''}</td><td>{'〇' if candidate_dup[18] else ''}</td></tr>
        <tr><td>リーダー</td><td>{'〇' if target[19] else ''}</td><td>{'〇' if candidate_dup[19] else ''}</td></tr>
        <tr><td>顧客折衝</td><td>{'〇' if target[20] else ''}</td><td>{'〇' if candidate_dup[20] else ''}</td></tr>

        <tr><td>開発</td><td>{'〇' if target[10] else ''}</td><td>{'〇' if candidate_dup[10] else ''}</td></tr>
        <tr><td>インフラ</td><td>{'〇' if target[11] else ''}</td><td>{'〇' if candidate_dup[11] else ''}</td></tr>
        <tr><td>運用保守</td><td>{'〇' if target[12] else ''}</td><td>{'〇' if candidate_dup[12] else ''}</td></tr>
        <tr><td>PMO</td><td>{'〇' if target[13] else ''}</td><td>{'〇' if candidate_dup[13] else ''}</td></tr>
        <tr><td>要件定義</td><td>{'〇' if target[14] else ''}</td><td>{'〇' if candidate_dup[14] else ''}</td></tr>
        <tr><td>設計</td><td>{'〇' if target[15] else ''}</td><td>{'〇' if candidate_dup[15] else ''}</td></tr>
        <tr><td>テスト</td><td>{'〇' if target[16] else ''}</td><td>{'〇' if candidate_dup[16] else ''}</td></tr>

        <tr><td>更新日</td><td>{target[33][:10] if target[33] else ''}</td><td>{candidate_dup[33][:10] if candidate_dup[33] else ''}</td></tr>

        <tr><td>メール</td><td>{mail_link_target}</td><td>{mail_link_dup}</td></tr>
        <tr><td>添付</td><td>{attachment_link_target}</td><td>{attachment_link_dup}</td></tr>

        <tr><td>確認理由</td><td>{target[31] or ""}</td><td>{candidate_dup[31]}</td></tr>

    </table>
    <br>
    
    <a href="/review/approve/{candidate_id}">
        <button>
            重複ではない
        </button>
    </a>

    <a href="/review/keep_new/{target[0]}/{candidate_dup[0]}">
        <button>今回のデータを残す</button>
    </a>

    <a href="/review/keep_old/{target[0]}/{candidate_dup[0]}">
        <button>既存データを残す</button>
    </a>

    <br><br>
    <button onclick="history.back()">戻る</button>

    </body>
    </html>
    """

    return HTMLResponse(content=html)

@app.get("/review/approve/{candidate_id}")
def approve_review(candidate_id: int):

    update_review_status(candidate_id)

    return RedirectResponse(
        url="/search",
        status_code=302
    )

@app.get("/review/keep_new/{target_id}/{duplicate_id}")
def keep_new(target_id: int, duplicate_id: int):

    delete_candidate(duplicate_id)

    update_review_status(target_id)

    return RedirectResponse(
        url="/search",
        status_code=302
    )

@app.get("/review/keep_old/{target_id}/{duplicate_id}")
def keep_old(target_id: int, duplicate_id: int):

    delete_candidate(target_id)

    update_review_status(duplicate_id)

    return RedirectResponse(
        url="/search",
        status_code=302
    )

@app.get("/edit/{candidate_id}", response_class=HTMLResponse)
def edit_candidate(candidate_id: int):

    row = get_candidate_by_id(candidate_id)

    if not row:
        return "候補者が存在しません"

    return f"""
    <html>
    <body>

    <h2>候補者編集</h2>

    <form method="post">

        名前<br>
        <input type="text"
            name="name"
            value="{row[1] or ''}">
        <br><br>

        年齢<br>
        <input type="text"
            name="age"
            value="{row[2] or ''}">
        <br><br>

        最寄駅<br>
        <input type="text"
            name="nearest"
            value="{row[3] or ''}">
        <br><br>

        会社<br>
        <input type="text"
            name="company"
            value="{row[4] or ''}">
        <br><br>

        商流<br>
        <select name="vendor_type">

            <option value=""
                {"selected" if not row[32] else ""}>
                未設定
            </option>

            <option value="直"
                {"selected" if row[32] == "直" else ""}>
                直
            </option>

            <option value="一社先"
                {"selected" if row[32] == "一社先" else ""}>
                一社先
            </option>

        </select>
        <br><br>

        単価<br>
        <input type="text"
            name="price"
            value="{row[6] or ''}">
        <br><br>

        稼働開始日<br>
        <input type="text"
            name="start"
            value="{row[7] or ''}">
        <br><br>

        リモート<br>
        <input type="text"
            name="remote"
            value="{row[8] or ''}">
        <br><br>

        経験年数<br>
        <input type="text"
            name="total_experience_years"
            value="{row[22] or ''}">
        <br><br>

        スキル<br>
        <textarea
            name="skills"
            rows="5"
            cols="80">{row[23] or ''}</textarea>
        <br><br>

        役割<br>
        <input type="text"
            name="role"
            value="{row[24] or ''}">
        <br><br>

        <br><br>

        上流経験
        <input type="checkbox"
            name="has_upstream"
            value="1"
            {"checked" if row[18] else ""}>

        <br><br>

        リーダー経験
        <input type="checkbox"
            name="has_leader"
            value="1"
            {"checked" if row[19] else ""}>

        <br><br>

        顧客折衝経験
        <input type="checkbox"
            name="has_client_contact"
            value="1"
            {"checked" if row[20] else ""}>

        <br><br>

        開発
        <input type="checkbox"
            name="exp_dev"
            value="1"
            {"checked" if row[10] else ""}>

        <br><br>

        インフラ
        <input type="checkbox"
            name="exp_infra"
            value="1"
            {"checked" if row[11] else ""}>

        <br><br>

        運用保守
        <input type="checkbox"
            name="exp_ops"
            value="1"
            {"checked" if row[12] else ""}>

        <br><br>

        PMO
        <input type="checkbox"
            name="exp_pmo"
            value="1"
            {"checked" if row[13] else ""}>

        <br><br>

        要件定義
        <input type="checkbox"
            name="exp_requirements"
            value="1"
            {"checked" if row[14] else ""}>

        <br><br>

        設計
        <input type="checkbox"
            name="exp_design"
            value="1"
            {"checked" if row[15] else ""}>

        <br><br>

        テスト
        <input type="checkbox"
            name="exp_test"
            value="1"
            {"checked" if row[16] else ""}>

        <input type="submit" value="保存">

    </form>

    <br>
    <button onclick="history.back()">戻る</button>

    </body>
    </html>
    """

@app.post("/edit/{candidate_id}")
def update_candidate_page(

    candidate_id: int,

    name: str = Form(""),
    age: str = Form(""),
    nearest: str = Form(""),
    company: str = Form(""),
    vendor_type: str = Form(""),

    price: str = Form(""),
    start: str = Form(""),
    remote: str = Form(""),
    total_experience_years: str = Form(""),

    skills: str = Form(""),
    role: str = Form(""),

    has_upstream: str = Form(""),
    has_leader: str = Form(""),
    has_client_contact: str = Form(""),

    exp_dev: str = Form(""),
    exp_infra: str = Form(""),
    exp_ops: str = Form(""),
    exp_pmo: str = Form(""),
    exp_requirements: str = Form(""),
    exp_design: str = Form(""),
    exp_test: str = Form("")
):

    data = {
        "id": candidate_id,
        "name": name,
        "age": age if age else None,
        "nearest": nearest,
        "company": company,
        "vendor_type": vendor_type,

        "price": price if price else None,
        "start": start,
        "remote": remote,
        "total_experience_years":
            float(total_experience_years)
            if total_experience_years
            else None,

        "skills": skills,
        "role": role,

        "has_upstream": bool(has_upstream),
        "has_leader": bool(has_leader),
        "has_client_contact": bool(has_client_contact),

        "exp_dev": bool(exp_dev),
        "exp_infra": bool(exp_infra),
        "exp_ops": bool(exp_ops),
        "exp_pmo": bool(exp_pmo),
        "exp_requirements": bool(exp_requirements),
        "exp_design": bool(exp_design),
        "exp_test": bool(exp_test),

        "updated_at": datetime.now().isoformat()
    }

    update_candidate(data)

    review_status, review_reason = check_review_needed(
        data,
        check_duplicate=False
    )


    data["review_status"] = review_status
    data["review_reason"] = review_reason

    update_candidate(data)

    return RedirectResponse(
        url="/search",
        status_code=302
    )